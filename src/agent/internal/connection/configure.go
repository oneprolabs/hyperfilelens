// Package connection changes an installed node's server without re-enrollment.
package connection

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/shirou/gopsutil/v4/process"
	"hyperfilelens/agent/internal/infra/config"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/atomicfile"
	"hyperfilelens/agent/internal/platform/vfs"
	_ "modernc.org/sqlite"
)

type lensInfo struct {
	UUID          string `json:"uuid"`
	BasePath      string `json:"base_path"`
	BaseURL       string `json:"base_url"`
	Connected     bool   `json:"connected"`
	Authenticated string `json:"last_authenticated_at"`
}
type serverInfo struct {
	NodeID         string    `json:"node_id"`
	Org            string    `json:"org_key"`
	Role           string    `json:"role"`
	InstallationID string    `json:"installation_id"`
	Busy           bool      `json:"busy"`
	Connected      bool      `json:"agent_connected"`
	Lens           *lensInfo `json:"lens"`
}
type savedFile struct {
	Path   string
	Data   []byte
	Exists bool
	Mode   uint32
	UID    int
	GID    int
}
type transaction struct {
	PID          int32
	Born         int64
	Phase        string
	Files        []savedFile
	AgentRunning bool
	LensRunning  bool
	Gateway      bool
}

// NormalizeServer accepts an origin only; URL credentials and paths are never stored.
func NormalizeServer(raw string) (string, string, error) {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Hostname() == "" || (u.Scheme != "https" && u.Scheme != "http") || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") || strings.ContainsAny(raw, "\r\n\t") || (u != nil && strings.ContainsAny(u.Host, "$\"'\\")) {
		return "", "", errors.New("server-url must be an HTTP(S) origin, without credentials, a path, query or fragment")
	}
	if u.Port() != "" {
		n, e := strconv.Atoi(u.Port())
		if e != nil || n < 1 || n > 65535 {
			return "", "", errors.New("invalid server port")
		}
	}
	u.Path = ""
	base := u.String()
	u.Scheme = "wss"
	if strings.HasPrefix(base, "http:") {
		u.Scheme = "ws"
	}
	u.Path = "/ws/node/agent/"
	return base, u.String(), nil
}

func info(ctx context.Context, client *http.Client, base string, cfg *model.AgentConfig) (*serverInfo, error) {
	req, e := http.NewRequestWithContext(ctx, "GET", base+"/api/v1/node/enrollment/connection-info?node_id="+url.QueryEscape(cfg.NodeID), nil)
	if e != nil {
		return nil, e
	}
	nonceBytes := make([]byte, 32)
	if _, e = rand.Read(nonceBytes); e != nil {
		return nil, e
	}
	nonce := hex.EncodeToString(nonceBytes)
	key := sha256.Sum256([]byte(cfg.NodeToken))
	message := strings.Join([]string{"node", nonce, cfg.NodeID, cfg.OrgKey, string(cfg.Role), cfg.InstallationID}, "\x00")
	signature := hmac.New(sha256.New, key[:])
	signature.Write([]byte(message))
	req.Header.Set("X-Org-Key", cfg.OrgKey)
	req.Header.Set("X-Connection-Nonce", nonce)
	req.Header.Set("X-Connection-Proof", hex.EncodeToString(signature.Sum(nil)))
	resp, e := client.Do(req)
	if e != nil {
		return nil, errors.New("new server connection failed; check routing and the existing TLS policy")
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("server identity/readiness check returned HTTP %d; no re-enrollment will be attempted", resp.StatusCode)
	}
	raw, e := io.ReadAll(io.LimitReader(resp.Body, 65537))
	if e != nil || len(raw) > 65536 {
		return nil, errors.New("invalid server response")
	}
	var envelope struct {
		Data json.RawMessage `json:"data"`
	}
	_ = json.Unmarshal(raw, &envelope)
	if len(envelope.Data) > 0 {
		raw = envelope.Data
	}
	var signed struct {
		Payload string `json:"payload"`
		Proof   string `json:"proof"`
	}
	if json.Unmarshal(raw, &signed) != nil {
		return nil, errors.New("server did not provide an installed-identity proof")
	}
	payload, decodeErr := base64.StdEncoding.DecodeString(signed.Payload)
	actual, proofErr := hex.DecodeString(signed.Proof)
	verifier := hmac.New(sha256.New, key[:])
	verifier.Write([]byte("server\x00" + nonce + "\x00"))
	verifier.Write(payload)
	if decodeErr != nil || proofErr != nil || !hmac.Equal(actual, verifier.Sum(nil)) {
		return nil, errors.New("target server proof does not match the installed identity")
	}
	raw = payload
	var result serverInfo
	if json.Unmarshal(raw, &result) != nil || result.NodeID != cfg.NodeID || result.Org != cfg.OrgKey || result.Role != string(cfg.Role) || result.InstallationID != cfg.InstallationID || cfg.InstallationID == "" {
		return nil, errors.New("target server does not match the installed node identity")
	}
	return &result, nil
}

func patchEnv(raw []byte, changes map[string]string) []byte {
	lines := strings.Split(string(raw), "\n")
	seen := map[string]bool{}
	for i, line := range lines {
		key, _, ok := strings.Cut(line, "=")
		key = strings.TrimSpace(key)
		if value, exists := changes[key]; ok && exists {
			lines[i] = key + "=" + strconv.Quote(value)
			seen[key] = true
		}
	}
	for _, key := range []string{"HFL_API_BASE", "HFL_WSS_URL", "LENS_BASE_URL"} {
		if value, ok := changes[key]; ok && !seen[key] {
			lines = append(lines, key+"="+strconv.Quote(value))
		}
	}
	return []byte(strings.TrimRight(strings.Join(lines, "\n"), "\n") + "\n")
}

func patchJSON(raw []byte, base, wss string) ([]byte, error) {
	var values map[string]json.RawMessage
	if json.Unmarshal(raw, &values) != nil || values == nil {
		return nil, errors.New("invalid config.json; existing configuration was not replaced")
	}
	values["api_base_url"], _ = json.Marshal(base)
	values["wss_url"], _ = json.Marshal(wss)
	b, e := json.MarshalIndent(values, "", "  ")
	return append(b, '\n'), e
}

func localIdle(root string) error {
	path := vfs.AgentDatabasePath(root)
	if _, e := os.Stat(path); e != nil {
		return fmt.Errorf("cannot verify local tasks: %w", e)
	}
	db, e := sql.Open("sqlite", "file:"+filepath.ToSlash(path)+"?mode=ro&_pragma=busy_timeout(3000)")
	if e != nil {
		return e
	}
	defer db.Close()
	var count int
	if e = db.QueryRow("SELECT count(*) FROM tasks WHERE status IN ('pending','running')").Scan(&count); e != nil {
		return errors.New("cannot verify local task state")
	}
	if count > 0 {
		return errors.New("node has active local tasks; wait for them to finish")
	}
	return nil
}

func save(path string, t transaction) error {
	b, e := json.Marshal(t)
	if e != nil {
		return e
	}
	if err := atomicfile.Write(path, b, 0600); err != nil {
		return err
	}
	if len(t.Files) > 0 && t.Files[0].Exists {
		return restoreOwner(path, t.Files[0].UID, t.Files[0].GID)
	}
	return nil
}
func snapshot(path string) (savedFile, error) {
	f := savedFile{Path: path, Mode: 0600, UID: -1, GID: -1}
	st, e := os.Stat(path)
	if os.IsNotExist(e) {
		return f, nil
	}
	if e != nil {
		return f, e
	}
	if !st.Mode().IsRegular() {
		return f, errors.New("configuration path is not a regular file")
	}
	f.Exists = true
	f.UID, f.GID = fileOwner(st)
	f.Mode = uint32(st.Mode().Perm())
	f.Data, e = os.ReadFile(path)
	return f, e
}
func restore(t transaction) error {
	var errs []error
	for _, f := range t.Files {
		var e error
		if f.Exists {
			e = atomicfile.Write(f.Path, f.Data, os.FileMode(f.Mode))
			if e == nil {
				e = restoreOwner(f.Path, f.UID, f.GID)
			}
		} else {
			e = os.Remove(f.Path)
			if os.IsNotExist(e) {
				e = nil
			}
		}
		if e != nil {
			errs = append(errs, e)
		}
	}
	return errors.Join(errs...)
}

// Serialize the idle check and maintenance marker with task admission.
func prepareWhenIdle(root string, t transaction) error {
	db, err := sql.Open("sqlite", "file:"+filepath.ToSlash(vfs.AgentDatabasePath(root))+"?mode=rw&_pragma=busy_timeout(3000)")
	if err != nil {
		return err
	}
	defer db.Close()
	tx, err := db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.Exec("UPDATE tasks SET status=status WHERE 0"); err != nil {
		return err
	}
	var count int
	if err = tx.QueryRow("SELECT count(*) FROM tasks WHERE status IN ('pending','running')").Scan(&count); err != nil {
		return err
	}
	if count > 0 {
		return errors.New("local work started during preflight; wait for it to finish")
	}
	if err = save(statePath(root), t); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		_ = os.Remove(statePath(root))
		return err
	}
	return nil
}

func statePath(root string) string {
	return filepath.Join(vfs.AgentLifecycleDir(root), "connection-state.json")
}
func ownerAlive(t transaction) bool {
	p, e := process.NewProcess(t.PID)
	if e != nil {
		return false
	}
	born, e := p.CreateTime()
	if e != nil {
		return true
	}
	return born == t.Born
}

// Recover restores a previously interrupted change before the daemon loads config.
func Recover(root string) error {
	raw, e := os.ReadFile(statePath(root))
	if os.IsNotExist(e) {
		return nil
	}
	if e != nil {
		return e
	}
	var t transaction
	if json.Unmarshal(raw, &t) != nil || t.PID <= 0 || t.Born <= 0 || (t.Phase != "prepared" && t.Phase != "applying") || len(t.Files) != len(configPaths(root, t.Gateway)) {
		return errors.New("invalid connection recovery state; contact support")
	}
	if ownerAlive(t) {
		return nil
	}
	allowed := map[string]bool{}
	for _, p := range configPaths(root, t.Gateway) {
		allowed[p] = true
	}
	for _, f := range t.Files {
		if !allowed[f.Path] {
			return errors.New("connection recovery contains an unexpected path")
		}
	}
	if e = restore(t); e != nil {
		return e
	}
	if t.Gateway && t.Phase != "prepared" {
		recoveryCtx, recoveryCancel := context.WithTimeout(context.Background(), 5*time.Minute)
		defer recoveryCancel()
		if e = applyLens(recoveryCtx, root, t.LensRunning); e != nil {
			return fmt.Errorf("old AI engine configuration restored but container recovery failed: %w", e)
		}
	}
	lock := filepath.Join(vfs.AgentLifecycleDir(root), "install.lock")
	pid, _ := os.ReadFile(filepath.Join(lock, "pid"))
	operation, _ := os.ReadFile(filepath.Join(lock, "operation"))
	if strings.TrimSpace(string(pid)) == strconv.Itoa(int(t.PID)) && strings.TrimSpace(string(operation)) == "configure" {
		if err := os.RemoveAll(lock); err != nil {
			return err
		}
	}
	return os.Remove(statePath(root))
}

func configPaths(root string, gateway bool) []string {
	paths := []string{filepath.Join(root, "config", "agent.env"), filepath.Join(root, "config", "config.json")}
	if gateway {
		paths = append(paths, filepath.Join(root, "config", "lensnode.env"), filepath.Join(root, "runtime", "lensnode", "docker-compose.yml"))
	}
	return paths
}

func recoverForCommand(ctx context.Context, root string, ops runtimeHooks) error {
	raw, err := os.ReadFile(statePath(root))
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	var previous transaction
	if json.Unmarshal(raw, &previous) != nil {
		return errors.New("invalid interrupted connection state")
	}
	if ownerAlive(previous) {
		return nil
	}
	if err = Recover(root); err != nil {
		return err
	}
	if previous.AgentRunning && previous.Phase != "prepared" {
		running, checkErr := ops.agentRunning(root)
		if checkErr != nil {
			return checkErr
		}
		if !running {
			if err = ops.agentService(ctx, root, "start"); err != nil {
				return fmt.Errorf("original configuration restored, but previous Agent state could not be resumed: %w", err)
			}
		}
	}
	return nil
}

// Configure is invoked by the installed lifecycle wrapper, not a download helper.
type runtimeHooks struct {
	agentRunning func(string) (bool, error)
	lensRunning  func(context.Context, string) (bool, error)
	agentService func(context.Context, string, string) error
	stopLens     func(context.Context, string) error
	applyLens    func(context.Context, string, bool) error
}

func Configure(ctx context.Context, rawURL string) error {
	err := configure(ctx, rawURL, runtimeHooks{agentRunning, lensRunning, agentService, stopLens, applyLens})
	root := vfs.DefaultAgentDataDir()
	if _, check := os.Stat(filepath.Join(root, "config", "agent.env")); check == nil {
		path := filepath.Join(root, "logs", "configure.log")
		if mkdirErr := os.MkdirAll(filepath.Dir(path), 0700); mkdirErr == nil {
			if log, openErr := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600); openErr == nil {
				message := "server configuration completed"
				if err != nil {
					message = err.Error()
				}
				_, _ = fmt.Fprintf(log, "%s %s\n", time.Now().UTC().Format(time.RFC3339Nano), message)
				_ = log.Close()
				fmt.Println("Log:", path)
			}
		}
	}
	return err
}
func configure(ctx context.Context, rawURL string, ops runtimeHooks) (err error) {
	base, wss, e := NormalizeServer(rawURL)
	if e != nil {
		return e
	}
	root := vfs.DefaultAgentDataDir()
	if e = recoverForCommand(ctx, root, ops); e != nil {
		return e
	}
	if _, e = os.Stat(filepath.Join(root, "config", "agent.env")); e != nil {
		return errors.New("installed Agent configuration not found")
	}
	store, e := config.NewStore(ctx, config.LoadOptions{})
	if e != nil {
		return e
	}
	cfg := store.Current()
	root = cfg.DataDir
	if root == "" {
		root = vfs.DefaultAgentDataDir()
	}
	if cfg.NodeID == "" || cfg.NodeToken == "" || cfg.InstallationID == "" {
		return errors.New("installed node identity is incomplete; this command does not register nodes")
	}
	if strings.HasPrefix(cfg.APIBaseURL, "https:") && strings.HasPrefix(base, "http:") {
		return errors.New("changing from HTTPS to HTTP is not allowed")
	}
	lock := filepath.Join(vfs.AgentLifecycleDir(root), "install.lock")
	if e = os.MkdirAll(filepath.Dir(lock), 0700); e != nil {
		return e
	}
	if e = os.Mkdir(lock, 0700); e != nil {
		return errors.New("another lifecycle operation or an interrupted lock exists; contact support")
	}
	defer os.RemoveAll(lock)
	if e = os.WriteFile(filepath.Join(lock, "operation"), []byte("configure"), 0600); e != nil {
		return e
	}
	if e = os.WriteFile(filepath.Join(lock, "pid"), []byte(strconv.Itoa(os.Getpid())), 0600); e != nil {
		return e
	}
	if _, e = os.Stat(filepath.Join(vfs.AgentLifecycleDir(root), "upgrade-state.json")); e == nil {
		return errors.New("pending upgrade must finish before changing server")
	}
	env, e := config.ParseEnvFile(filepath.Join(root, "config", "agent.env"))
	if e != nil {
		return e
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: env["HFL_INSECURE_TLS"] != "0"}
	client := &http.Client{Timeout: 10 * time.Second, Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	fmt.Println("Checking the new server and installed identity…")
	target, e := info(ctx, client, base, cfg)
	if e != nil {
		return e
	}
	if target.Busy {
		return errors.New("server reports active work; wait for it to finish")
	}
	if e = localIdle(root); e != nil {
		return e
	}
	gateway := cfg.Role == model.RoleGateway
	paths := configPaths(root, gateway)
	updates := map[string][]byte{}
	t := transaction{PID: int32(os.Getpid()), Gateway: gateway, Phase: "prepared"}
	p, e := process.NewProcess(t.PID)
	if e != nil {
		return e
	}
	t.Born, e = p.CreateTime()
	if e != nil {
		return e
	}
	for _, path := range paths {
		f, e := snapshot(path)
		if e != nil {
			return e
		}
		t.Files = append(t.Files, f)
	}
	updates[paths[0]] = patchEnv(t.Files[0].Data, map[string]string{"HFL_API_BASE": base, "HFL_WSS_URL": wss})
	if t.Files[1].Exists {
		updates[paths[1]], e = patchJSON(t.Files[1].Data, base, wss)
		if e != nil {
			return e
		}
	}
	if gateway {
		if target.Lens == nil || !t.Files[2].Exists || !t.Files[3].Exists {
			return errors.New("existing AI engine configuration is incomplete; no install will be attempted")
		}
		lensEnv, e := config.ParseEnvFile(paths[2])
		if e != nil {
			return e
		}
		if target.Lens.UUID != lensEnv["LENSNODE_UUID"] || lensEnv["LENSNODE_TOKEN"] == "" {
			return errors.New("AI engine identity mismatch")
		}
		lensBase := target.Lens.BaseURL
		if target.Lens.BasePath != "" {
			if target.Lens.BasePath != "/sourcelens" {
				return errors.New("unsupported bundled AI engine base path")
			}
			lensBase = base + target.Lens.BasePath
		}
		u, e := url.Parse(lensBase)
		if e != nil || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || strings.ContainsAny(lensBase, "$\r\n\t\"") || (u.Scheme != "https" && u.Scheme != "http") {
			return errors.New("invalid AI engine address")
		}
		updates[paths[2]] = patchEnv(t.Files[2].Data, map[string]string{"LENS_BASE_URL": lensBase})
		re := regexp.MustCompile(`(?m)^([ \t]*LENSNODE_SERVER_URL:)[^\r\n]*`)
		if len(re.FindAll(t.Files[3].Data, -1)) != 1 {
			return errors.New("unsupported AI engine Compose configuration")
		}
		updates[paths[3]] = re.ReplaceAll(t.Files[3].Data, []byte("${1} "+strconv.Quote(lensBase)))
		t.LensRunning, e = ops.lensRunning(ctx, root)
		if e != nil {
			return e
		}
	}
	t.AgentRunning, e = ops.agentRunning(root)
	if e != nil {
		return e
	}
	// Exact same configuration is a no-op, never an implicit upgrade/restart.
	changed := false
	for _, f := range t.Files {
		if b, ok := updates[f.Path]; ok && string(b) != string(f.Data) {
			changed = true
		}
	}
	if !changed {
		fmt.Println("Server configuration is already current; no services changed.")
		return nil
	}
	backup := filepath.Join(root, "backup", "connection-"+time.Now().UTC().Format("20060102T150405.000000000Z"))
	if e = os.MkdirAll(backup, 0700); e != nil {
		return e
	}
	if e = save(filepath.Join(backup, "state.json"), t); e != nil {
		return e
	}
	fmt.Println("Backup:", backup)
	if e = prepareWhenIdle(root, t); e != nil {
		return e
	}
	defer func() {
		if err == nil {
			return
		}
		rollbackCtx, rollbackCancel := context.WithTimeout(context.Background(), 5*time.Minute)
		defer rollbackCancel()
		var rollbackErr error
		if t.AgentRunning {
			rollbackErr = ops.agentService(rollbackCtx, root, "stop")
		}
		if gateway && t.LensRunning {
			rollbackErr = errors.Join(rollbackErr, ops.stopLens(rollbackCtx, root))
		}
		if rollbackErr != nil {
			err = errors.Join(err, errors.New("could not quiesce components for recovery; backup and recovery state retained"))
			return
		}
		rollbackErr = restore(t)
		if gateway {
			rollbackErr = errors.Join(rollbackErr, ops.applyLens(rollbackCtx, root, t.LensRunning))
		}
		if t.AgentRunning {
			rollbackErr = errors.Join(rollbackErr, ops.agentService(rollbackCtx, root, "start"))
		}
		if rollbackErr == nil {
			_ = os.Remove(statePath(root))
			err = fmt.Errorf("%w; original configuration restored (original connectivity is not guaranteed)", err)
		} else {
			err = errors.Join(err, fmt.Errorf("recovery incomplete; backup retained at %s: %w", backup, rollbackErr))
		}
	}()
	t.Phase = "applying"
	if e = save(statePath(root), t); e != nil {
		return e
	}
	if t.AgentRunning {
		if e = ops.agentService(ctx, root, "stop"); e != nil {
			return e
		}
	}
	if gateway && t.LensRunning {
		fresh, checkErr := info(ctx, client, base, cfg)
		if checkErr != nil {
			return checkErr
		}
		if fresh.Busy {
			return errors.New("work started during preflight; configuration will not be applied")
		}
		if e = ops.stopLens(ctx, root); e != nil {
			return e
		}
	}
	if e = localIdle(root); e != nil {
		return e
	}
	for _, f := range t.Files {
		if b, ok := updates[f.Path]; ok {
			if e = atomicfile.Write(f.Path, b, os.FileMode(f.Mode)); e != nil {
				return e
			}
			if f.Exists {
				if e = restoreOwner(f.Path, f.UID, f.GID); e != nil {
					return e
				}
			}
		}
	}
	if e = store.Reload(ctx); e != nil {
		return e
	}
	if store.Current().APIBaseURL != base || store.Current().WSSURL != wss {
		return errors.New("an effective configuration override prevents the new address")
	}
	started := time.Now().UTC()
	if gateway {
		if e = ops.applyLens(ctx, root, t.LensRunning); e != nil {
			return e
		}
	}
	if t.AgentRunning {
		if e = ops.agentService(ctx, root, "start"); e != nil {
			return e
		}
	}
	if t.AgentRunning || t.LensRunning {
		deadline := time.Now().Add(60 * time.Second)
		verified := false
		for time.Now().Before(deadline) {
			observed, e := info(ctx, client, base, cfg)
			if e == nil {
				agentOK := !t.AgentRunning || connectionObserved(root, wss, started) && observed.Connected
				lensOK := !t.LensRunning || observed.Lens != nil && observed.Lens.UUID == target.Lens.UUID && observed.Lens.Connected && observed.Lens.Authenticated != "" && observed.Lens.Authenticated != target.Lens.Authenticated
				if agentOK && lensOK {
					verified = true
					break
				}
			}
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(time.Second):
			}
		}
		if !verified {
			return errors.New("new Agent/AI engine connection was not verified before the deadline")
		}
	}
	if e = os.Remove(statePath(root)); e != nil {
		return e
	}
	fmt.Println("Server configuration updated. Identity, credentials, data and versions preserved.")
	if !t.AgentRunning {
		fmt.Println("Agent was stopped and remains stopped.")
	}
	if gateway && !t.LensRunning {
		fmt.Println("AI engine was stopped and remains stopped.")
	}
	return nil
}
func connectionObserved(root, wss string, since time.Time) bool {
	raw, e := os.ReadFile(filepath.Join(root, "runtime", "connection.json"))
	if e != nil {
		return false
	}
	var state struct {
		URL         string
		ConnectedAt time.Time
	}
	return json.Unmarshal(raw, &state) == nil && state.URL == wss && !state.ConnectedAt.Before(since)
}
