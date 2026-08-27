package engine

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
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
	"sync"
	"time"

	"hyperfilelens/agent/internal/platform/process"
	"hyperfilelens/agent/internal/platform/vfs"
)

// Certificate lifetime is deliberately much longer than an individual
// backup/restore.  The Session owns the key material and removes it when the
// server stops; expiry is not used as a lifecycle mechanism.
const repositoryServerCertificateLifetime = 365 * 24 * time.Hour
const repositoryServerStartupTimeout = 30 * time.Second
const repositoryServerStopTimeout = 5 * time.Second

const (
	repositoryServerPortMin = 51515
	repositoryServerPortMax = 52014
)

type repositoryServerSession struct {
	SessionID   string `json:"session_id"`
	PID         int    `json:"pid"`
	Port        int    `json:"port,omitempty"`
	Fingerprint string `json:"fingerprint,omitempty"`
	URL         string `json:"url"`
	StartedAt   string `json:"started_at"`
}

type repositoryServerProcess struct {
	cmd         *exec.Cmd
	done        chan struct{}
	release     func()
	waitErr     error
	mu          sync.Mutex
	url         string
	username    string
	password    string
	fingerprint string
	port        int
	listenHost  string
	automatic   bool
	logFile     string
}

var repositoryServerPortState = struct {
	sync.Mutex
	ports map[int]struct{}
}{ports: make(map[int]struct{})}

var repositoryServerSessionLocks = struct {
	sync.Mutex
	locks map[string]*repositoryServerSessionLock
}{locks: make(map[string]*repositoryServerSessionLock)}
var repositoryServerProcesses sync.Map // map[string]*repositoryServerProcess

type repositoryServerSessionLock struct {
	mu   sync.Mutex
	refs int
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
	releaseSessionLock := acquireRepositoryServerSessionLock(sessionID)
	defer releaseSessionLock()
	if value, ok := repositoryServerProcesses.Load(sessionID); ok {
		managed := value.(*repositoryServerProcess)
		if managed.cmd != nil && managed.cmd.Process != nil &&
			managed.url != "" && repositoryServerTLSReady(managed.listenHost, managed.port, managed.fingerprint) {
			return "success", map[string]any{
				"session_id": sessionID, "server_url": managed.url, "url": managed.url,
				"username": managed.username, "password": managed.password,
				"server_cert_fingerprint": managed.fingerprint, "pid": managed.cmd.Process.Pid,
				"log_file": managed.logFile, "port": managed.port,
				"port_range_min": repositoryServerPortMin, "port_range_max": repositoryServerPortMax,
			}, ""
		}
		select {
		case <-managed.done:
			if repositoryServerProcesses.CompareAndDelete(sessionID, managed) && managed.automatic {
				e.releaseRepositoryServerPort(managed.port)
			}
			cleanupRepositoryServerSessionFiles(e.repositoryServerSessionDir(sessionID), filepath.Join(e.repositoryServerSessionDir(sessionID), "server.crt"), filepath.Join(e.repositoryServerSessionDir(sessionID), "server.key"))
		default:
			return "failed", map[string]any{"error_code": "REPOSITORY_SERVER_SESSION_CONFLICT"}, "REPOSITORY_SERVER_SESSION_CONFLICT: Repository Server Session is already running but is not ready"
		}
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
	if err := e.reconcilePersistedRepositoryServerSession(sessionID, sessionDir); err != nil {
		return "failed", result, err.Error()
	}
	if err := ensureKopiaServerUser(ctx, bin, configFile, env, username, password); err != nil {
		return "failed", result, err.Error()
	}

	certFile := filepath.Join(sessionDir, "server.crt")
	keyFile := filepath.Join(sessionDir, "server.key")
	logFile := filepath.Join(sessionDir, "server.log")
	// Generate the certificate before starting Kopia.  This avoids Kopia's
	// expensive implicit RSA generation on a busy proxy.
	fingerprint, err := prepareRepositoryServerCertificate(certFile, keyFile, publicHost)
	if err != nil {
		return "failed", result, "REPOSITORY_SERVER_TLS_PREPARE_FAILED: " + err.Error()
	}
	cleanupOnFailure := true
	defer func() {
		if cleanupOnFailure {
			cleanupRepositoryServerSessionFiles(sessionDir, certFile, keyFile)
		}
	}()
	if automaticPort {
		free, err := e.reserveRepositoryServerPort(listenHost)
		if err != nil {
			return "failed", result, "REPOSITORY_SERVER_PORT_UNAVAILABLE: " + err.Error()
		}
		port = free
		defer func() {
			if cleanupOnFailure {
				e.releaseRepositoryServerPort(port)
			}
		}()
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
	logHandle, err := os.OpenFile(logFile, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return "failed", result, err.Error()
	}
	defer logHandle.Close()

	cmd := exec.Command(bin, args...)
	if err := process.Configure(ctx, cmd, process.Options{}); err != nil {
		_ = os.Remove(certFile)
		_ = os.Remove(keyFile)
		return "failed", result, "REPOSITORY_SERVER_PROCESS_START_FAILED: " + err.Error()
	}
	cmd.Env = append(os.Environ(), envMapToList(env)...)
	cmd.Stdout = logHandle
	cmd.Stderr = logHandle
	if err := cmd.Start(); err != nil {
		return "failed", result, "REPOSITORY_SERVER_PROCESS_START_FAILED: " + err.Error()
	}
	releaseLifetime, err := process.BindLifetime(cmd)
	if err != nil {
		stopDeadline := time.Now().Add(repositoryServerStopTimeout)
		waitDone := make(chan struct{})
		go func() {
			_ = cmd.Wait()
			close(waitDone)
		}()
		killCtx, cancelKill := context.WithDeadline(context.Background(), stopDeadline)
		killErr := process.KillGroup(killCtx, cmd.Process.Pid)
		cancelKill()
		if killErr != nil {
			_ = cmd.Process.Kill()
		}
		remaining := time.Until(stopDeadline)
		if remaining > 0 {
			timer := time.NewTimer(remaining)
			select {
			case <-waitDone:
				timer.Stop()
			case <-timer.C:
			}
		}
		message := "REPOSITORY_SERVER_PROCESS_START_FAILED: " + err.Error()
		if killErr != nil {
			message += "; stop failed: " + killErr.Error()
		}
		return "failed", result, message
	}
	url := "https://" + net.JoinHostPort(publicHost, strconv.Itoa(port))
	managedProcess := &repositoryServerProcess{
		cmd: cmd, done: make(chan struct{}), release: releaseLifetime,
		url: url, username: username, password: password, fingerprint: fingerprint,
		port: port, listenHost: listenHost, automatic: automaticPort, logFile: logFile,
	}
	repositoryServerProcesses.Store(sessionID, managedProcess)
	go func() {
		err := cmd.Wait()
		managedProcess.mu.Lock()
		managedProcess.waitErr = err
		managedProcess.mu.Unlock()
		managedProcess.release()
		close(managedProcess.done)
		releaseSessionLock := acquireRepositoryServerSessionLock(sessionID)
		if repositoryServerProcesses.CompareAndDelete(sessionID, managedProcess) {
			if automaticPort {
				e.releaseRepositoryServerPort(port)
			}
			cleanupRepositoryServerSessionFiles(sessionDir, certFile, keyFile)
		}
		releaseSessionLock()
	}()
	readyFingerprint, waitErr := waitForRepositoryServerReady(ctx, listenHost, port, fingerprint, logFile, managedProcess)
	if waitErr != nil {
		if stopErr := stopRepositoryServerProcess(managedProcess); stopErr != nil {
			cleanupOnFailure = false
			return "failed", result, waitErr.Error() + "; " + stopErr.Error()
		}
		repositoryServerProcesses.Delete(sessionID)
		return "failed", result, waitErr.Error()
	}
	managedProcess.fingerprint = readyFingerprint
	session := repositoryServerSession{
		SessionID:   sessionID,
		PID:         cmd.Process.Pid,
		Port:        port,
		Fingerprint: readyFingerprint,
		URL:         url,
		StartedAt:   time.Now().UTC().Format(time.RFC3339),
	}
	if err := writeRepositoryServerSession(e.repositoryServerSessionStatePath(sessionID), session); err != nil {
		if stopErr := stopRepositoryServerProcess(managedProcess); stopErr != nil {
			cleanupOnFailure = false
			return "failed", result, "REPOSITORY_SERVER_SESSION_STATE_FAILED: " + err.Error() + "; " + stopErr.Error()
		}
		repositoryServerProcesses.Delete(sessionID)
		return "failed", result, "REPOSITORY_SERVER_SESSION_STATE_FAILED: " + err.Error()
	}
	select {
	case <-managedProcess.done:
		managedProcess.mu.Lock()
		exitErr := managedProcess.waitErr
		managedProcess.mu.Unlock()
		repositoryServerProcesses.Delete(sessionID)
		return "failed", result, fmt.Sprintf(
			"REPOSITORY_SERVER_PROCESS_EXITED: Kopia Repository Server exited after readiness: %v; %s",
			exitErr,
			tailFile(logFile, 20),
		)
	default:
	}
	cleanupOnFailure = false
	slog.InfoContext(ctx, "repository server started", "task_id", taskID, "session_id", sessionID, "pid", cmd.Process.Pid, "url", url)
	result["session_id"] = sessionID
	result["server_url"] = url
	result["url"] = url
	result["username"] = username
	result["password"] = password
	result["server_cert_fingerprint"] = readyFingerprint
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
	releaseSessionLock := acquireRepositoryServerSessionLock(sessionID)
	defer releaseSessionLock()
	statePath := e.repositoryServerSessionStatePath(sessionID)
	session, err := readRepositoryServerSession(statePath)
	value, tracked := repositoryServerProcesses.LoadAndDelete(sessionID)
	if err != nil && !tracked {
		if os.IsNotExist(err) {
			return "success", map[string]any{"session_id": sessionID, "already_stopped": true}, ""
		}
		return "failed", map[string]any{
			"session_id": sessionID,
			"error_code": "REPOSITORY_SERVER_STOP_FAILED",
		}, "REPOSITORY_SERVER_STOP_FAILED: Repository Server Session state is unreadable"
	}
	if tracked {
		managed := value.(*repositoryServerProcess)
		if stopErr := stopRepositoryServerProcess(managed); stopErr != nil {
			select {
			case <-managed.done:
				// The process exited at the stop timeout boundary; finish the
				// idempotent cleanup instead of restoring a stale live Session.
			default:
				repositoryServerProcesses.Store(sessionID, managed)
				return "failed", map[string]any{"session_id": sessionID, "error_code": "REPOSITORY_SERVER_STOP_FAILED"}, stopErr.Error()
			}
		}
	} else if err == nil && (session.PID > 0 || session.Port > 0 || session.URL != "") {
		if reconcileErr := e.reconcilePersistedRepositoryServerSession(sessionID, e.repositoryServerSessionDir(sessionID)); reconcileErr != nil {
			return "failed", map[string]any{"session_id": sessionID, "error_code": "REPOSITORY_SERVER_STOP_FAILED"}, reconcileErr.Error()
		}
		slog.InfoContext(ctx, "repository server stale session reconciled", "session_id", sessionID, "pid", session.PID)
	}
	if tracked {
		managed := value.(*repositoryServerProcess)
		if managed.automatic && managed.port > 0 {
			e.releaseRepositoryServerPort(managed.port)
		}
	}
	cleanupRepositoryServerSessionFiles(e.repositoryServerSessionDir(sessionID), filepath.Join(e.repositoryServerSessionDir(sessionID), "server.crt"), filepath.Join(e.repositoryServerSessionDir(sessionID), "server.key"))
	_ = os.Remove(statePath)
	slog.InfoContext(ctx, "repository server stopped", "session_id", sessionID, "pid", session.PID)
	return "success", map[string]any{"session_id": sessionID, "pid": session.PID}, ""
}

// reconcilePersistedRepositoryServerSession handles state left by an earlier
// Agent lifetime. A PID alone is never trusted: an active listener must first
// prove the expected TLS fingerprint before it can be terminated.
func (e *Engine) reconcilePersistedRepositoryServerSession(sessionID, sessionDir string) error {
	statePath := e.repositoryServerSessionStatePath(sessionID)
	session, err := readRepositoryServerSession(statePath)
	if err != nil {
		if os.IsNotExist(err) {
			cleanupRepositoryServerSessionFiles(
				sessionDir,
				filepath.Join(sessionDir, "server.crt"),
				filepath.Join(sessionDir, "server.key"),
			)
			return nil
		}
		return fmt.Errorf("REPOSITORY_SERVER_SESSION_CONFLICT: existing Session state is unreadable")
	}
	if strings.TrimSpace(session.SessionID) != strings.TrimSpace(sessionID) {
		return fmt.Errorf("REPOSITORY_SERVER_SESSION_CONFLICT: existing Session identity does not match")
	}
	port := session.Port
	if port <= 0 {
		port, _ = neturlPort(session.URL)
	}
	if port > 0 && repositoryServerPortListening(port) {
		fingerprint := session.Fingerprint
		legacySession := fingerprint == ""
		if fingerprint == "" {
			fingerprint, _ = certificateFingerprint(filepath.Join(sessionDir, "server.crt"))
		}
		if fingerprint == "" || !repositoryServerTLSReady("", port, fingerprint) {
			return fmt.Errorf("REPOSITORY_SERVER_SESSION_CONFLICT: existing Session is still using port %d", port)
		}
		if session.PID <= 0 {
			return fmt.Errorf("REPOSITORY_SERVER_SESSION_CONFLICT: existing Session has no verifiable process")
		}
		stopDeadline := time.Now().Add(repositoryServerStopTimeout)
		var stopErr error
		if legacySession {
			var legacyProcess *os.Process
			legacyProcess, stopErr = os.FindProcess(session.PID)
			if stopErr == nil {
				stopErr = legacyProcess.Kill()
			}
		} else {
			killCtx, cancelKill := context.WithDeadline(context.Background(), stopDeadline)
			stopErr = process.KillGroup(killCtx, session.PID)
			cancelKill()
		}
		if stopErr != nil {
			return fmt.Errorf("REPOSITORY_SERVER_SESSION_CONFLICT: could not stop previous Session: %w", stopErr)
		}
		for time.Now().Before(stopDeadline) && repositoryServerPortListening(port) {
			time.Sleep(100 * time.Millisecond)
		}
		if repositoryServerPortListening(port) {
			return fmt.Errorf("REPOSITORY_SERVER_SESSION_CONFLICT: previous Session did not release port %d", port)
		}
	}
	cleanupRepositoryServerSessionFiles(sessionDir, filepath.Join(sessionDir, "server.crt"), filepath.Join(sessionDir, "server.key"))
	return nil
}

func neturlPort(raw string) (int, error) {
	addr := strings.TrimSpace(raw)
	if strings.HasPrefix(addr, "https://") {
		addr = strings.TrimPrefix(addr, "https://")
	}
	_, port, err := net.SplitHostPort(addr)
	if err != nil {
		return 0, err
	}
	return strconv.Atoi(port)
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

func waitForRepositoryServerReady(ctx context.Context, listenHost string, port int, expectedFingerprint string, logFile string, managed *repositoryServerProcess) (string, error) {
	deadline := time.NewTimer(repositoryServerStartupTimeout)
	defer deadline.Stop()
	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return "", fmt.Errorf("REPOSITORY_SERVER_START_CANCELED: %w", ctx.Err())
		case <-managed.done:
			managed.mu.Lock()
			err := managed.waitErr
			managed.mu.Unlock()
			if err == nil {
				return "", fmt.Errorf("REPOSITORY_SERVER_PROCESS_EXITED: Kopia Repository Server exited before becoming ready: %s", tailFile(logFile, 20))
			}
			return "", fmt.Errorf("REPOSITORY_SERVER_PROCESS_EXITED: Kopia Repository Server exited before becoming ready: %w; %s", err, tailFile(logFile, 20))
		case <-deadline.C:
			return "", fmt.Errorf("REPOSITORY_SERVER_READY_TIMEOUT: Kopia Repository Server did not become ready within %s: %s", repositoryServerStartupTimeout, tailFile(logFile, 20))
		case <-ticker.C:
			if repositoryServerTLSReady(listenHost, port, expectedFingerprint) {
				return expectedFingerprint, nil
			}
		}
	}
}

func repositoryServerTLSReady(listenHost string, port int, expectedFingerprint string) bool {
	host := strings.TrimSpace(listenHost)
	if host == "" || host == "0.0.0.0" {
		host = "127.0.0.1"
	} else if host == "::" {
		host = "::1"
	}
	dialer := &net.Dialer{Timeout: 750 * time.Millisecond}
	conn, err := tls.DialWithDialer(
		dialer,
		"tcp",
		net.JoinHostPort(host, strconv.Itoa(port)),
		&tls.Config{InsecureSkipVerify: true}, //nolint:gosec // loopback probe verifies the pinned SHA-256 fingerprint below
	)
	if err != nil {
		return false
	}
	defer conn.Close()
	if len(conn.ConnectionState().PeerCertificates) == 0 {
		return false
	}
	sum := sha256.Sum256(conn.ConnectionState().PeerCertificates[0].Raw)
	return strings.EqualFold(hex.EncodeToString(sum[:]), expectedFingerprint)
}

func repositoryServerPortListening(port int) bool {
	for _, host := range []string{"127.0.0.1", "::1"} {
		conn, err := net.DialTimeout("tcp", net.JoinHostPort(host, strconv.Itoa(port)), 500*time.Millisecond)
		if err == nil {
			_ = conn.Close()
			return true
		}
	}
	return false
}

func prepareRepositoryServerCertificate(certFile, keyFile, host string) (string, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return "", err
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return "", err
	}
	now := time.Now().UTC()
	template := &x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: "HyperFileLens Repository Server"},
		NotBefore:    now.Add(-time.Minute), NotAfter: now.Add(repositoryServerCertificateLifetime),
		KeyUsage:    x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		DNSNames:    []string{"localhost"},
		IPAddresses: []net.IP{net.ParseIP("127.0.0.1"), net.ParseIP("::1")},
	}
	if parsed := net.ParseIP(strings.TrimSpace(host)); parsed != nil {
		template.IPAddresses = append(template.IPAddresses, parsed)
	} else if strings.TrimSpace(host) != "" && host != "0.0.0.0" && host != "::" {
		template.DNSNames = append(template.DNSNames, strings.TrimSuffix(strings.TrimSpace(host), "."))
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		return "", err
	}
	keyDER, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return "", err
	}
	if err := atomicWriteRepositoryServerFile(certFile, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), 0o600); err != nil {
		return "", err
	}
	if err := atomicWriteRepositoryServerFile(keyFile, pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER}), 0o600); err != nil {
		_ = os.Remove(certFile)
		return "", err
	}
	sum := sha256.Sum256(der)
	return strings.ToUpper(hex.EncodeToString(sum[:])), nil
}

func atomicWriteRepositoryServerFile(path string, data []byte, mode os.FileMode) error {
	tmp, err := os.CreateTemp(filepath.Dir(path), ".repository-server-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if err := tmp.Chmod(mode); err != nil {
		_ = tmp.Close()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, path)
}

func stopRepositoryServerProcess(managed *repositoryServerProcess) error {
	if managed == nil || managed.cmd == nil || managed.cmd.Process == nil {
		return nil
	}
	select {
	case <-managed.done:
		return nil
	default:
	}
	stopDeadline := time.Now().Add(repositoryServerStopTimeout)
	killCtx, cancelKill := context.WithDeadline(context.Background(), stopDeadline)
	killErr := process.KillGroup(killCtx, managed.cmd.Process.Pid)
	cancelKill()
	if killErr != nil {
		select {
		case <-managed.done:
			return nil
		default:
		}
	}
	remaining := time.Until(stopDeadline)
	if remaining <= 0 {
		if killErr != nil {
			return fmt.Errorf("REPOSITORY_SERVER_STOP_FAILED: stop Kopia Repository Server: %w", killErr)
		}
		return fmt.Errorf("REPOSITORY_SERVER_STOP_FAILED: Kopia Repository Server did not exit within %s", repositoryServerStopTimeout)
	}
	timer := time.NewTimer(remaining)
	defer timer.Stop()
	select {
	case <-managed.done:
		return nil
	case <-timer.C:
		if killErr != nil {
			return fmt.Errorf("REPOSITORY_SERVER_STOP_FAILED: stop Kopia Repository Server: %w", killErr)
		}
		return fmt.Errorf("REPOSITORY_SERVER_STOP_FAILED: Kopia Repository Server did not exit within %s", repositoryServerStopTimeout)
	}
}

func cleanupRepositoryServerSessionFiles(sessionDir, certFile, keyFile string) {
	_ = os.Remove(certFile)
	_ = os.Remove(keyFile)
	_ = os.Remove(filepath.Join(sessionDir, "session.json"))
	if leftovers, err := filepath.Glob(filepath.Join(sessionDir, ".repository-server-*")); err == nil {
		for _, leftover := range leftovers {
			_ = os.Remove(leftover)
		}
	}
}

func acquireRepositoryServerSessionLock(sessionID string) func() {
	key := sanitizeSessionToken(sessionID)
	repositoryServerSessionLocks.Lock()
	lock := repositoryServerSessionLocks.locks[key]
	if lock == nil {
		lock = &repositoryServerSessionLock{}
		repositoryServerSessionLocks.locks[key] = lock
	}
	lock.refs++
	repositoryServerSessionLocks.Unlock()
	lock.mu.Lock()
	var once sync.Once
	return func() {
		once.Do(func() {
			lock.mu.Unlock()
			repositoryServerSessionLocks.Lock()
			lock.refs--
			if lock.refs == 0 {
				delete(repositoryServerSessionLocks.locks, key)
			}
			repositoryServerSessionLocks.Unlock()
		})
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
	return atomicWriteRepositoryServerFile(path, data, 0o600)
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
	// Production Engines are task-scoped, so reservations must be shared by
	// the Agent process rather than kept only on one Engine instance. Tests can
	// still provide an explicit map to exercise exhaustion deterministically.
	ports := e.repositoryServerPorts
	if ports != nil {
		e.repositoryServerMu.Lock()
		defer e.repositoryServerMu.Unlock()
	} else {
		repositoryServerPortState.Lock()
		defer repositoryServerPortState.Unlock()
		ports = repositoryServerPortState.ports
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
		if _, reserved := ports[port]; reserved {
			continue
		}
		if !available(bindHost, port) {
			continue
		}
		ports[port] = struct{}{}
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
	if e.repositoryServerPorts != nil {
		e.repositoryServerMu.Lock()
		defer e.repositoryServerMu.Unlock()
		delete(e.repositoryServerPorts, port)
		return
	}
	repositoryServerPortState.Lock()
	defer repositoryServerPortState.Unlock()
	delete(repositoryServerPortState.ports, port)
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
