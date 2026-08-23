package engine

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"log/slog"
	"math/big"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"hyperfilelens/agent/internal/platform/process"
	"hyperfilelens/agent/internal/platform/vfs"
)

const repositoryServerStartupTimeout = 15 * time.Second

const (
	repositoryServerPortMin = 51515
	repositoryServerPortMax = 52014
)

type repositoryServerSession struct {
	SessionID string `json:"session_id"`
	PID       int    `json:"pid"`
	URL       string `json:"url"`
	StartedAt string `json:"started_at"`
}

func (e *Engine) runRepositoryServerStart(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
) (string, map[string]any, string) {
	sessionID := strings.TrimSpace(payloadStringValue(p.Extra["session_id"]))
	if sessionID == "" {
		sessionID = taskID
	}
	username := strings.TrimSpace(payloadStringValue(p.Extra["username"]))
	if username == "" {
		username = "hfl-" + sanitizeSessionToken(sessionID) + "@localhost"
	}
	username = normalizeKopiaServerUsername(username)
	password := strings.TrimSpace(payloadStringValue(p.Extra["password"]))
	if password == "" {
		password = randomToken(32)
	}
	listenHost := strings.TrimSpace(payloadStringValue(p.Extra["listen_host"]))
	if listenHost == "" {
		listenHost = "0.0.0.0"
	}
	publicHost := strings.TrimSpace(payloadStringValue(p.Extra["public_host"]))
	if publicHost == "" {
		publicHost = listenHost
		if publicHost == "" || publicHost == "0.0.0.0" || publicHost == "::" {
			publicHost, _ = os.Hostname()
		}
	}
	port := 0
	automaticPort := true
	if value, ok := payloadIntValue(p.Extra["port"]); ok {
		port = value
		automaticPort = port <= 0
	}

	configFile, env, result, _, prepErr := e.prepareManagedRepository(ctx, rep, taskID, p, repositoryPrepareConnect)
	if prepErr != "" {
		return "failed", result, prepErr
	}
	if result == nil {
		result = map[string]any{}
	}
	bin, err := e.kopiaBin(ctx)
	if err != nil {
		return "failed", result, err.Error()
	}
	sessionDir := e.repositoryServerSessionDir(sessionID)
	if err := os.MkdirAll(sessionDir, 0o700); err != nil {
		return "failed", result, err.Error()
	}
	if err := ensureKopiaServerUser(ctx, bin, configFile, env, username, password); err != nil {
		return "failed", result, err.Error()
	}

	certFile := filepath.Join(sessionDir, "server.crt")
	keyFile := filepath.Join(sessionDir, "server.key")
	logFile := filepath.Join(sessionDir, "server.log")
	if automaticPort {
		free, err := e.reserveRepositoryServerPort(listenHost)
		if err != nil {
			return "failed", result, err.Error()
		}
		port = free
		defer e.releaseRepositoryServerPort(port)
	}
	address := net.JoinHostPort(listenHost, strconv.Itoa(port))
	args := []string{
		"server", "start",
		"--config-file=" + configFile,
		"--address=" + address,
		"--ui",
		"--grpc",
		"--tls-cert-file=" + certFile,
		"--tls-key-file=" + keyFile,
		"--server-username=" + username,
		"--server-password=" + password,
	}
	if _, statErr := os.Stat(certFile); os.IsNotExist(statErr) {
		args = append(args, "--tls-generate-cert")
	}
	logHandle, err := os.OpenFile(logFile, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return "failed", result, err.Error()
	}
	defer logHandle.Close()

	cmd := exec.Command(bin, args...)
	cmd.Env = append(os.Environ(), envMapToList(env)...)
	cmd.Stdout = logHandle
	cmd.Stderr = logHandle
	if err := cmd.Start(); err != nil {
		return "failed", result, err.Error()
	}
	exitCh := make(chan error, 1)
	go func() {
		exitCh <- cmd.Wait()
	}()

	fingerprint, waitErr := waitForServerCertificate(ctx, certFile, logFile, exitCh)
	if waitErr != nil {
		_ = cmd.Process.Kill()
		return "failed", result, waitErr.Error()
	}
	url := "https://" + net.JoinHostPort(publicHost, strconv.Itoa(port))
	session := repositoryServerSession{
		SessionID: sessionID,
		PID:       cmd.Process.Pid,
		URL:       url,
		StartedAt: time.Now().UTC().Format(time.RFC3339),
	}
	if err := writeRepositoryServerSession(e.repositoryServerSessionStatePath(sessionID), session); err != nil {
		_ = cmd.Process.Kill()
		return "failed", result, err.Error()
	}
	slog.InfoContext(ctx, "repository server started", "task_id", taskID, "session_id", sessionID, "pid", cmd.Process.Pid, "url", url)
	result["session_id"] = sessionID
	result["server_url"] = url
	result["url"] = url
	result["username"] = username
	result["password"] = password
	result["server_cert_fingerprint"] = fingerprint
	result["pid"] = cmd.Process.Pid
	result["log_file"] = logFile
	result["port"] = port
	result["port_range_min"] = repositoryServerPortMin
	result["port_range_max"] = repositoryServerPortMax
	return "success", result, ""
}

func (e *Engine) runRepositoryServerStop(ctx context.Context, p Payload) (string, map[string]any, string) {
	sessionID := strings.TrimSpace(payloadStringValue(p.Extra["session_id"]))
	if sessionID == "" {
		if repositoryRaw, ok := p.Extra["repository"].(map[string]any); ok {
			sessionID = strings.TrimSpace(payloadStringValue(repositoryRaw["session_id"]))
		}
	}
	if sessionID == "" {
		return "failed", nil, "session_id is required"
	}
	statePath := e.repositoryServerSessionStatePath(sessionID)
	session, err := readRepositoryServerSession(statePath)
	if err != nil {
		return "success", map[string]any{"session_id": sessionID, "already_stopped": true}, ""
	}
	if session.PID > 0 {
		if proc, findErr := os.FindProcess(session.PID); findErr == nil {
			_ = proc.Kill()
		}
	}
	_ = os.Remove(statePath)
	slog.InfoContext(ctx, "repository server stopped", "session_id", sessionID, "pid", session.PID)
	return "success", map[string]any{"session_id": sessionID, "pid": session.PID}, ""
}

func ensureKopiaServerUser(
	ctx context.Context,
	bin string,
	configFile string,
	env map[string]string,
	username string,
	password string,
) error {
	args := []string{
		"--config-file=" + configFile,
		"server", "users", "add", username,
		"--user-password=" + password,
	}
	res, err := process.Run(ctx, bin, args, env, "")
	if err == nil {
		return nil
	}
	msg := strings.ToLower(strings.Join([]string{res.Stdout, res.Stderr, err.Error()}, "\n"))
	if !strings.Contains(msg, "exist") && !strings.Contains(msg, "already") {
		return fmt.Errorf("create kopia server user: %s", processErrorMessage(res, err))
	}
	setArgs := []string{
		"--config-file=" + configFile,
		"server", "users", "set", username,
		"--user-password=" + password,
	}
	setRes, setErr := process.Run(ctx, bin, setArgs, env, "")
	if setErr != nil {
		return fmt.Errorf("update kopia server user: %s", processErrorMessage(setRes, setErr))
	}
	return nil
}

func waitForServerCertificate(ctx context.Context, certFile string, logFile string, exitCh <-chan error) (string, error) {
	deadline := time.NewTimer(repositoryServerStartupTimeout)
	defer deadline.Stop()
	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case err := <-exitCh:
			if err == nil {
				return "", fmt.Errorf("kopia server exited before becoming ready: %s", tailFile(logFile, 20))
			}
			return "", fmt.Errorf("kopia server exited before becoming ready: %w; %s", err, tailFile(logFile, 20))
		case <-deadline.C:
			return "", fmt.Errorf("kopia server did not create TLS certificate within %s: %s", repositoryServerStartupTimeout, tailFile(logFile, 20))
		case <-ticker.C:
			fingerprint, err := certificateFingerprint(certFile)
			if err == nil && fingerprint != "" {
				return fingerprint, nil
			}
		}
	}
}

func tailFile(path string, maxLines int) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	text := strings.TrimSpace(string(data))
	if text == "" {
		return ""
	}
	lines := strings.Split(text, "\n")
	if maxLines > 0 && len(lines) > maxLines {
		lines = lines[len(lines)-maxLines:]
	}
	return strings.TrimSpace(strings.Join(lines, "\n"))
}

func certificateFingerprint(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	block, _ := pem.Decode(data)
	if block == nil {
		return "", fmt.Errorf("invalid certificate PEM")
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(cert.Raw)
	return strings.ToUpper(hex.EncodeToString(sum[:])), nil
}

func (e *Engine) repositoryServerSessionDir(sessionID string) string {
	base := strings.TrimSpace(e.current().DataDir)
	if base == "" {
		base = os.TempDir()
	}
	return filepath.Join(vfs.AgentRuntimeDir(base), "workspace", "server-sessions", sanitizeSessionToken(sessionID))
}

func (e *Engine) repositoryServerSessionStatePath(sessionID string) string {
	return filepath.Join(e.repositoryServerSessionDir(sessionID), "session.json")
}

func writeRepositoryServerSession(path string, session repositoryServerSession) error {
	data, err := json.MarshalIndent(session, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o600)
}

func readRepositoryServerSession(path string) (repositoryServerSession, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return repositoryServerSession{}, err
	}
	var session repositoryServerSession
	if err := json.Unmarshal(data, &session); err != nil {
		return repositoryServerSession{}, err
	}
	return session, nil
}

func (e *Engine) reserveRepositoryServerPort(host string) (int, error) {
	return e.reserveRepositoryServerPortWithProbe(host, repositoryServerPortAvailable)
}

func (e *Engine) reserveRepositoryServerPortWithProbe(
	host string,
	available func(host string, port int) bool,
) (int, error) {
	e.repositoryServerMu.Lock()
	defer e.repositoryServerMu.Unlock()
	if e.repositoryServerPorts == nil {
		e.repositoryServerPorts = make(map[int]struct{})
	}

	bindHost := strings.TrimSpace(host)
	if bindHost == "" || bindHost == "0.0.0.0" || bindHost == "::" {
		bindHost = ""
	}
	count := repositoryServerPortMax - repositoryServerPortMin + 1
	start := 0
	if random, err := rand.Int(rand.Reader, big.NewInt(int64(count))); err == nil {
		start = int(random.Int64())
	}
	for offset := 0; offset < count; offset++ {
		port := repositoryServerPortMin + (start+offset)%count
		if _, reserved := e.repositoryServerPorts[port]; reserved {
			continue
		}
		if !available(bindHost, port) {
			continue
		}
		e.repositoryServerPorts[port] = struct{}{}
		return port, nil
	}
	return 0, fmt.Errorf(
		"no available Repository Server port in TCP range %d-%d",
		repositoryServerPortMin,
		repositoryServerPortMax,
	)
}

func repositoryServerPortAvailable(host string, port int) bool {
	listener, err := net.Listen("tcp", net.JoinHostPort(host, strconv.Itoa(port)))
	if err != nil {
		return false
	}
	_ = listener.Close()
	return true
}

func (e *Engine) releaseRepositoryServerPort(port int) {
	e.repositoryServerMu.Lock()
	defer e.repositoryServerMu.Unlock()
	delete(e.repositoryServerPorts, port)
}

func randomToken(length int) string {
	const alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
	if length <= 0 {
		length = 32
	}
	var b strings.Builder
	for b.Len() < length {
		n, err := rand.Int(rand.Reader, big.NewInt(int64(len(alphabet))))
		if err != nil {
			return fmt.Sprintf("%d", time.Now().UnixNano())
		}
		b.WriteByte(alphabet[n.Int64()])
	}
	return b.String()
}

func sanitizeSessionToken(value string) string {
	var b strings.Builder
	for _, r := range strings.TrimSpace(value) {
		if r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '-' || r == '_' {
			b.WriteRune(r)
		} else {
			b.WriteByte('-')
		}
	}
	out := strings.Trim(b.String(), "-")
	if out == "" {
		return "session"
	}
	if len(out) > 64 {
		return out[:64]
	}
	return out
}

func normalizeKopiaServerUsername(value string) string {
	raw := strings.ToLower(strings.TrimSpace(value))
	if raw == "" {
		raw = "hfl-backup@localhost"
	}
	parts := strings.SplitN(raw, "@", 2)
	user := sanitizeKopiaUsernamePart(parts[0], "hfl-backup")
	host := "localhost"
	if len(parts) == 2 {
		host = sanitizeKopiaUsernamePart(parts[1], "localhost")
	}
	return user + "@" + host
}

func sanitizeKopiaUsernamePart(value string, fallback string) string {
	var b strings.Builder
	for _, r := range strings.ToLower(strings.TrimSpace(value)) {
		if r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || r == '-' || r == '_' || r == '.' {
			b.WriteRune(r)
		} else {
			b.WriteByte('-')
		}
	}
	out := strings.Trim(b.String(), "-.")
	if out == "" {
		return fallback
	}
	return out
}

func envMapToList(values map[string]string) []string {
	out := make([]string, 0, len(values))
	for key, value := range values {
		out = append(out, key+"="+value)
	}
	return out
}

func processErrorMessage(res process.Result, err error) string {
	msg := strings.TrimSpace(res.Stderr)
	if msg == "" {
		msg = strings.TrimSpace(res.Stdout)
	}
	if msg == "" && err != nil {
		msg = err.Error()
	}
	if msg == "" {
		msg = fmt.Sprintf("exit code %d", res.ExitCode)
	}
	return msg
}
