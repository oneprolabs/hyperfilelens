package connection

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"hyperfilelens/agent/internal/infra/config"
	"hyperfilelens/agent/internal/infra/database"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/atomicfile"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestNormalizeServer(t *testing.T) {
	for _, raw := range []string{"https://host:11443/", "https://[::1]:11443", "http://private.example"} {
		base, ws, e := NormalizeServer(raw)
		if e != nil || strings.HasSuffix(base, "/") || !strings.HasSuffix(ws, "/ws/node/agent/") {
			t.Fatalf("%s: %s %s %v", raw, base, ws, e)
		}
	}
	for _, raw := range []string{"host:11443", "https://u:p@host", "https://host/api/", "https://host?token=x", "https://host#x", "https://host:0", "https://host:65536", "https://$HOST", "https://host\n"} {
		if _, _, e := NormalizeServer(raw); e == nil {
			t.Errorf("accepted %q", raw)
		}
	}
}
func TestPatchPreservesIdentity(t *testing.T) {
	raw := []byte("# comment\nHFL_NODE_CREDENTIAL=secret\nHFL_NODE_ID=3\nUNKNOWN_FLAG=value\nHFL_API_BASE=old\n")
	patched := patchEnv(raw, map[string]string{"HFL_API_BASE": "https://new", "HFL_WSS_URL": "wss://new/ws/node/agent/"})
	for _, keep := range []string{"# comment", "HFL_NODE_CREDENTIAL=secret", "HFL_NODE_ID=3", "UNKNOWN_FLAG=value"} {
		if !strings.Contains(string(patched), keep) {
			t.Fatal("lost", keep)
		}
	}
	j, e := patchJSON([]byte(`{"node_id":"3","node_token":"secret","extra":{"keep":true},"api_base_url":"old"}`), "https://new", "wss://new/ws/node/agent/")
	if e != nil {
		t.Fatal(e)
	}
	var values map[string]any
	_ = json.Unmarshal(j, &values)
	if values["node_id"] != "3" || values["node_token"] != "secret" || values["extra"] == nil {
		t.Fatal("lost JSON fields")
	}
	if _, e = patchJSON([]byte(`[]`), "x", "y"); e == nil {
		t.Fatal("accepted invalid overlay")
	}
}
func fixture(t *testing.T, role model.Role) (string, *serverInfo, runtimeHooks) {
	t.Helper()
	return fixtureAtRoot(t, role, t.TempDir())
}
func fixtureAtRoot(t *testing.T, role model.Role, root string) (string, *serverInfo, runtimeHooks) {
	t.Helper()
	t.Setenv("HFL_DATA_DIR", root)
	t.Setenv("HFL_AGENT_ROOT", root)
	t.Setenv("HFL_INSTALLATION_MODE", "system")
	t.Setenv("HFL_AGENT_HOME", "")
	e := config.WriteEnvFile(filepath.Join(root, "config", "agent.env"), map[string]string{"HFL_DATA_DIR": root, "HFL_AGENT_ROOT": root, "HFL_NODE_ID": "3", "HFL_ORG_KEY": "org", "HFL_NODE_CREDENTIAL": "secret", "HFL_INSTALLATION_ID": "installed", "HFL_NODE_ROLE": string(role), "HFL_API_BASE": "http://old.invalid", "HFL_WSS_URL": "ws://old.invalid/ws/node/agent/", "HFL_INSTALLATION_MODE": "system"})
	if e != nil {
		t.Fatal(e)
	}
	e = atomicfile.Write(filepath.Join(root, "config", "config.json"), []byte(`{"node_id":"3","node_token":"secret","api_base_url":"http://old.invalid","extra":true}`), 0600)
	if e != nil {
		t.Fatal(e)
	}
	db, e := database.Open(context.Background(), filepath.Join(root, "data", "agent.db"))
	if e != nil {
		t.Fatal(e)
	}
	_ = db.Close()
	target := &serverInfo{NodeID: "3", Org: "org", Role: string(role), InstallationID: "installed", Connected: true}
	ops := runtimeHooks{agentRunning: func(string) (bool, error) { return false, nil }, lensRunning: func(context.Context, string) (bool, error) { return false, nil }, agentService: func(context.Context, string, string) error { return nil }, stopLens: func(context.Context, string) error { return nil }, applyLens: func(context.Context, string, bool) error { return nil }}
	return root, target, ops
}
func serve(t *testing.T, target *serverInfo) *httptest.Server {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Node-Token") != "" || r.URL.Query().Get("node_id") != "3" {
			t.Error("missing installed authentication")
		}
		payload, _ := json.Marshal(target)
		key := sha256.Sum256([]byte("secret"))
		mac := hmac.New(sha256.New, key[:])
		mac.Write([]byte("server\x00" + r.Header.Get("X-Connection-Nonce") + "\x00"))
		mac.Write(payload)
		_ = json.NewEncoder(w).Encode(map[string]any{"code": 0, "data": map[string]string{"payload": base64.StdEncoding.EncodeToString(payload), "proof": hex.EncodeToString(mac.Sum(nil))}})
	}))
	t.Cleanup(server.Close)
	return server
}
func TestConfigurePersistsBothLayers(t *testing.T) {
	root, target, ops := fixture(t, model.RoleAgent)
	server := serve(t, target)
	if e := configure(context.Background(), server.URL, ops); e != nil {
		t.Fatal(e)
	}
	store, e := config.NewStore(context.Background(), config.LoadOptions{})
	if e != nil || store.Current().APIBaseURL != server.URL || store.Current().NodeID != "3" || store.Current().NodeToken != "secret" {
		t.Fatal("effective configuration lost identity", e)
	}
	if e = configure(context.Background(), server.URL, ops); e != nil {
		t.Fatal(e)
	}
	if _, e = os.Stat(statePath(root)); !os.IsNotExist(e) {
		t.Fatal("transaction remained", e)
	}
}
func TestUnsafeTargetsDoNotWrite(t *testing.T) {
	for _, kind := range []string{"node", "org", "role", "installation", "busy"} {
		t.Run(kind, func(t *testing.T) {
			root, target, ops := fixture(t, model.RoleAgent)
			switch kind {
			case "node":
				target.NodeID = "4"
			case "org":
				target.Org = "other"
			case "role":
				target.Role = "gateway"
			case "installation":
				target.InstallationID = "another"
			case "busy":
				target.Busy = true
			}
			before, _ := os.ReadFile(filepath.Join(root, "config", "agent.env"))
			if e := configure(context.Background(), serve(t, target).URL, ops); e == nil {
				t.Fatal("accepted unsafe target")
			}
			after, _ := os.ReadFile(filepath.Join(root, "config", "agent.env"))
			if string(before) != string(after) {
				t.Fatal("changed before validation")
			}
		})
	}
}
func TestRollbackOnRestartFailure(t *testing.T) {
	root, target, ops := fixture(t, model.RoleAgent)
	ops.agentRunning = func(string) (bool, error) { return true, nil }
	ops.agentService = func(_ context.Context, _ string, action string) error {
		if action == "start" {
			return errors.New("cannot start")
		}
		return nil
	}
	before, _ := os.ReadFile(filepath.Join(root, "config", "agent.env"))
	if e := configure(context.Background(), serve(t, target).URL, ops); e == nil {
		t.Fatal("expected failure")
	}
	after, _ := os.ReadFile(filepath.Join(root, "config", "agent.env"))
	if string(before) != string(after) {
		t.Fatal("did not roll back")
	}
}
func TestActiveLocalTaskRejected(t *testing.T) {
	root, target, ops := fixture(t, model.RoleAgent)
	db, e := database.Open(context.Background(), filepath.Join(root, "data", "agent.db"))
	if e != nil {
		t.Fatal(e)
	}
	repo := database.NewTaskRepo(db)
	if e = repo.RecordCommand(context.Background(), database.RecordInput{TaskID: "active", Kind: "backup.run"}); e != nil {
		t.Fatal(e)
	}
	_ = db.Close()
	if e = configure(context.Background(), serve(t, target).URL, ops); e == nil || !strings.Contains(e.Error(), "active local") {
		t.Fatal(e)
	}
}
func TestGatewayPreservesImageAndCredentials(t *testing.T) {
	root, target, ops := fixture(t, model.RoleGateway)
	target.Lens = &lensInfo{UUID: "lens-1", BasePath: "/sourcelens"}
	lp := filepath.Join(root, "config", "lensnode.env")
	cp := filepath.Join(root, "runtime", "lensnode", "docker-compose.yml")
	_ = atomicfile.Write(lp, []byte("LENSNODE_UUID=lens-1\nLENSNODE_TOKEN=lens-secret\nLENS_BASE_URL=http://old.invalid/sourcelens\n"), 0600)
	_ = atomicfile.Write(cp, []byte("services:\n  lensnode:\n    image: pinned:0.61.3\n    environment:\n      LENSNODE_SERVER_URL: http://old.invalid/sourcelens\n      LENSNODE_TOKEN: lens-secret\n"), 0600)
	server := serve(t, target)
	if e := configure(context.Background(), server.URL, ops); e != nil {
		t.Fatal(e)
	}
	for _, path := range []string{lp, cp} {
		b, _ := os.ReadFile(path)
		if !strings.Contains(string(b), "lens-secret") {
			t.Fatal("lost Lens identity")
		}
	}
	env, e := config.ParseEnvFile(lp)
	if e != nil || env["LENS_BASE_URL"] != server.URL+"/sourcelens" {
		t.Fatal("host-side AI engine URL did not follow Agent", e)
	}
	b, _ := os.ReadFile(cp)
	if !strings.Contains(string(b), "pinned:0.61.3") ||
		!strings.Contains(string(b), strings.Replace(server.URL, "127.0.0.1", "host.docker.internal", 1)+"/sourcelens") ||
		!strings.Contains(string(b), `"host.docker.internal:host-gateway"`) {
		t.Fatal("changed version or lost container-side host routing")
	}
}

func TestGatewayLoopbackRollbackRestoresOriginalRouting(t *testing.T) {
	root, target, ops := fixture(t, model.RoleGateway)
	target.Lens = &lensInfo{UUID: "lens-1", BasePath: "/sourcelens"}
	lp := filepath.Join(root, "config", "lensnode.env")
	cp := filepath.Join(root, "runtime", "lensnode", "docker-compose.yml")
	oldLens := []byte("LENSNODE_UUID=lens-1\nLENSNODE_TOKEN=lens-secret\nLENS_BASE_URL=http://old.invalid/sourcelens\n")
	oldCompose := []byte(strings.Replace(managedCompose, "unchanged-secret", "lens-secret", 1))
	if err := atomicfile.Write(lp, oldLens, 0600); err != nil {
		t.Fatal(err)
	}
	if err := atomicfile.Write(cp, oldCompose, 0600); err != nil {
		t.Fatal(err)
	}
	server := serve(t, target)
	calls := 0
	ops.applyLens = func(_ context.Context, _ string, running bool) error {
		calls++
		if running {
			t.Fatal("originally stopped LensNode was started")
		}
		if calls == 1 {
			raw, err := os.ReadFile(cp)
			if err != nil || !strings.Contains(string(raw), "host.docker.internal:host-gateway") {
				t.Fatal("missing container routing before apply", err)
			}
			return errors.New("simulated Compose apply failure")
		}
		return nil
	}
	ops.agentService = func(context.Context, string, string) error {
		t.Fatal("originally stopped Agent was restarted")
		return nil
	}
	if err := configure(context.Background(), server.URL, ops); err == nil {
		t.Fatal("expected Compose apply failure")
	}
	for path, before := range map[string][]byte{lp: oldLens, cp: oldCompose} {
		after, err := os.ReadFile(path)
		if err != nil || string(after) != string(before) {
			t.Fatal("rollback did not restore original routing", path, err)
		}
	}
	if calls != 2 {
		t.Fatalf("previous LensNode configuration was not reapplied: %d calls", calls)
	}
	if _, err := os.Stat(statePath(root)); !os.IsNotExist(err) {
		t.Fatal("successful rollback retained maintenance marker", err)
	}
}

func TestRecoverInterruptedChange(t *testing.T) {
	root, _, _ := fixture(t, model.RoleAgent)
	var files []savedFile
	for _, p := range configPaths(root, false) {
		f, e := snapshot(p)
		if e != nil {
			t.Fatal(e)
		}
		files = append(files, f)
	}
	state := transaction{PID: 2147483646, Born: 1, Phase: "applying", Files: files}
	if e := save(statePath(root), state); e != nil {
		t.Fatal(e)
	}
	_ = atomicfile.Write(files[0].Path, []byte("broken"), 0600)
	if e := Recover(root); e != nil {
		t.Fatal(e)
	}
	b, _ := os.ReadFile(files[0].Path)
	if string(b) != string(files[0].Data) {
		t.Fatal("not restored")
	}
}
func TestOldConnectionObservationRejected(t *testing.T) {
	root := t.TempDir()
	_ = atomicfile.Write(filepath.Join(root, "runtime", "connection.json"), []byte(`{"URL":"wss://old/ws/node/agent/","ConnectedAt":"2000-01-01T00:00:00Z"}`), 0600)
	if connectionObserved(root, "wss://new/ws/node/agent/", time.Now()) {
		t.Fatal("accepted stale observation")
	}
}

func TestRunningAgentMustConfirmNewConnection(t *testing.T) {
	root, target, ops := fixture(t, model.RoleProxy)
	server := serve(t, target)
	ops.agentRunning = func(string) (bool, error) { return true, nil }
	ops.agentService = func(_ context.Context, _ string, action string) error {
		if action == "start" {
			_, wss, e := NormalizeServer(server.URL)
			if e != nil {
				return e
			}
			b, _ := json.Marshal(struct {
				URL         string
				ConnectedAt time.Time
			}{wss, time.Now().UTC()})
			return atomicfile.Write(filepath.Join(root, "runtime", "connection.json"), b, 0600)
		}
		return nil
	}
	if e := configure(context.Background(), server.URL, ops); e != nil {
		t.Fatal(e)
	}
	if _, e := os.Stat(statePath(root)); !os.IsNotExist(e) {
		t.Fatal("state not committed", e)
	}
}
func TestVerificationFailureRestoresOldAddress(t *testing.T) {
	root, target, ops := fixture(t, model.RoleAgent)
	server := serve(t, target)
	ops.agentRunning = func(string) (bool, error) { return true, nil }
	before, _ := os.ReadFile(filepath.Join(root, "config", "agent.env"))
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Millisecond)
	defer cancel()
	if e := configure(ctx, server.URL, ops); e == nil {
		t.Fatal("accepted missing connection observation")
	}
	after, _ := os.ReadFile(filepath.Join(root, "config", "agent.env"))
	if string(before) != string(after) {
		t.Fatal("verification failure did not restore files")
	}
}
func TestExternalSLAddressAndBindingArePreserved(t *testing.T) {
	root, target, ops := fixture(t, model.RoleGateway)
	target.Lens = &lensInfo{UUID: "external-lens", BaseURL: "https://external-sl.example:11445"}
	lp := filepath.Join(root, "config", "lensnode.env")
	cp := filepath.Join(root, "runtime", "lensnode", "docker-compose.yml")
	_ = atomicfile.Write(lp, []byte("LENSNODE_UUID=external-lens\nLENSNODE_TOKEN=unchanged\nLENS_BASE_URL=https://external-sl.example:11445\n"), 0600)
	_ = atomicfile.Write(cp, []byte("services:\n  lensnode:\n    image: pinned\n    environment:\n      LENSNODE_SERVER_URL: https://external-sl.example:11445\n"), 0600)
	if e := configure(context.Background(), serve(t, target).URL, ops); e != nil {
		t.Fatal(e)
	}
	b, _ := os.ReadFile(cp)
	if !strings.Contains(string(b), "external-sl.example:11445") {
		t.Fatal("replaced independent SL origin")
	}
	target.Lens.UUID = "wrong"
	if e := configure(context.Background(), serve(t, target).URL, ops); e == nil {
		t.Fatal("accepted a different LensNode identity")
	}
}
func TestLifecycleConflictAndPendingUpgradeReject(t *testing.T) {
	for _, kind := range []string{"lock", "upgrade"} {
		t.Run(kind, func(t *testing.T) {
			root, target, ops := fixture(t, model.RoleAgent)
			dir := filepath.Join(root, "lifecycle")
			_ = os.MkdirAll(dir, 0700)
			if kind == "lock" {
				_ = os.Mkdir(filepath.Join(dir, "install.lock"), 0700)
			} else {
				_ = os.WriteFile(filepath.Join(dir, "upgrade-state.json"), []byte(`{}`), 0600)
			}
			if e := configure(context.Background(), serve(t, target).URL, ops); e == nil {
				t.Fatal("ignored maintenance conflict")
			}
		})
	}
}
func TestDoesNotFollowCredentialRedirects(t *testing.T) {
	_, _, _ = fixture(t, model.RoleAgent)
	hit := false
	other := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { hit = true }))
	defer other.Close()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, other.URL, 302) }))
	defer server.Close()
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	_, err := info(context.Background(), client, server.URL, &model.AgentConfig{NodeID: "3", OrgKey: "org", NodeToken: "secret", InstallationID: "installed", Role: model.RoleAgent})
	if err == nil || hit {
		t.Fatal("redirected installed credentials")
	}
}

func TestMaintenanceMarkerBlocksTaskAdmission(t *testing.T) {
	root, _, _ := fixture(t, model.RoleAgent)
	var files []savedFile
	for _, path := range configPaths(root, false) {
		f, e := snapshot(path)
		if e != nil {
			t.Fatal(e)
		}
		files = append(files, f)
	}
	if e := prepareWhenIdle(root, transaction{PID: int32(os.Getpid()), Born: 1, Phase: "prepared", Files: files}); e != nil {
		t.Fatal(e)
	}
	db, e := database.Open(context.Background(), filepath.Join(root, "data", "agent.db"))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	_, _, e = database.NewTaskRepo(db).AcceptCommand(context.Background(), database.RecordInput{TaskID: "arriving", Kind: "backup.run"})
	if !errors.Is(e, database.ErrConnectionMaintenance) {
		t.Fatalf("admitted work during configure: %v", e)
	}
}
func TestServerProofCannotReflectTheClientProof(t *testing.T) {
	_, _, _ = fixture(t, model.RoleAgent)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		payload := []byte(`{"node_id":"3","org_key":"org","role":"agent","installation_id":"installed"}`)
		_ = json.NewEncoder(w).Encode(map[string]string{"payload": base64.StdEncoding.EncodeToString(payload), "proof": r.Header.Get("X-Connection-Proof")})
	}))
	defer server.Close()
	_, e := info(context.Background(), server.Client(), server.URL, &model.AgentConfig{NodeID: "3", OrgKey: "org", NodeToken: "secret", Role: model.RoleAgent, InstallationID: "installed"})
	if e == nil {
		t.Fatal("accepted reflected client authentication as server identity")
	}
}
