//go:build linux

package cli

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"hyperfilelens/agent/internal/wire"
)

// This opt-in test must run in an ephemeral container, never against a real
// installation. It exercises an unmodified legacy binary and its generated
// detached runner with the actual incoming installer, without a WS reconnect.
func TestLegacyAgentReportsPreStopFailureWithoutReconnect(t *testing.T) {
	if os.Getenv("HFL_LEGACY_CONTAINER_TEST") != "1" {
		t.Skip("requires explicitly isolated legacy-Agent container fixture")
	}
	old := os.Getenv("HFL_LEGACY_AGENT_BINARY")
	candidate := os.Getenv("HFL_CANDIDATE_AGENT_BINARY")
	if old == "" || candidate == "" {
		t.Fatal("fixture binaries are required")
	}
	root := "/opt/hyperfilelens-agent"
	if err := os.Mkdir(root, 0700); err != nil {
		t.Fatalf("refusing to touch an existing installation: %v", err)
	}
	write := func(name string, raw []byte, mode os.FileMode) {
		t.Helper()
		name = filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(name), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(name, raw, mode); err != nil {
			t.Fatal(err)
		}
	}
	oldRaw, err := os.ReadFile(old)
	if err != nil {
		t.Fatal(err)
	}
	write("bin/hfl-agent", oldRaw, 0700)
	write("bin/kopia", []byte("#!/bin/sh\nexit 0\n"), 0700)
	// Emulate only the supported host's service-manager queries. The upgrade
	// is rejected before stop; this fixture never starts/stops a real service.
	if err := os.MkdirAll("/run/systemd/system", 0700); err != nil {
		t.Fatal(err)
	}
	write("bin/systemctl", []byte("#!/bin/sh\ncase \"$1\" in\nshow-environment|start) exit 0 ;;\nis-active) echo active; exit 0 ;;\nstop|kill) echo unexpected-service-mutation >&2; exit 99 ;;\n*) exit 1 ;;\nesac\n"), 0700)

	write("INSTALLED_VERSION", []byte("0.3.12"), 0600)
	write("MANIFEST.json", []byte(`{"agent_commit":"e5df5b077448f973e97fcbd2c99bc61b8b4ff4d3"}`), 0600)
	write("lifecycle/upgrade-state.json", []byte(`{"phase":"rollback_failed","previous_version":"0.3.12","target_version":"0.3.21","lifecycle_was_running":1,"lifecycle_was_enabled":1,"state_snapshot_ready":1,"deployment_started":1}`), 0600)
	installer, err := os.ReadFile("../../packaging/install/install.sh")
	if err != nil {
		t.Fatal(err)
	}
	newRaw, err := os.ReadFile(candidate)
	if err != nil {
		t.Fatal(err)
	}
	files := map[string][]byte{"bin/hfl-agent": newRaw, "bin/kopia": []byte("#!/bin/sh\nexit 0\n"), "install.sh": installer}
	sums := map[string]string{}
	for name, raw := range files {
		sum := sha256.Sum256(raw)
		sums[name] = "sha256:" + hex.EncodeToString(sum[:])
	}
	manifest, _ := json.Marshal(map[string]any{"schema": 1, "agent_version": "0.3.99", "agent_commit": "15991599", "platform": "linux", "arch": "amd64", "bundle_flavor": "standard", "kopia_binary_sha256": sums["bin/kopia"], "files": sums})
	files["MANIFEST.json"] = manifest
	var bundle bytes.Buffer
	gz := gzip.NewWriter(&bundle)
	tw := tar.NewWriter(gz)
	for name, raw := range files {
		if err := tw.WriteHeader(&tar.Header{Name: "hfl-agent-test/" + name, Mode: 0700, Typeflag: tar.TypeReg, Size: int64(len(raw))}); err != nil {
			t.Fatal(err)
		}
		if _, err := tw.Write(raw); err != nil {
			t.Fatal(err)
		}
	}
	_ = tw.Close()
	_ = gz.Close()
	done := make(chan struct{})
	defer close(done)
	result := make(chan map[string]any, 1)
	serverErr := make(chan error, 1)
	var connections, handoffs atomic.Int32
	mux := http.NewServeMux()
	server := httptest.NewServer(mux)
	defer server.Close()
	mux.HandleFunc("/package", func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write(bundle.Bytes()) })
	mux.HandleFunc("/ws", func(w http.ResponseWriter, r *http.Request) {
		connections.Add(1)
		ws, err := (&websocket.Upgrader{Subprotocols: []string{wire.TaskResultAckSubprotocol}}).Upgrade(w, r, nil)
		if err != nil {
			serverErr <- err
			return
		}
		defer ws.Close()
		if err := ws.WriteJSON(map[string]any{"type": "task.command", "task_id": "15990000-0000-4000-8000-000000000001", "kind": "agent.upgrade", "node_id": 1, "payload": map[string]any{"target_version": "0.3.99", "target_commit": "15991599", "download_url": server.URL + "/package"}}); err != nil {
			serverErr <- err
			return
		}
		_ = ws.SetReadDeadline(time.Now().Add(90 * time.Second))
		for {
			var frame map[string]any
			if err := ws.ReadJSON(&frame); err != nil {
				serverErr <- err
				return
			}
			if frame["type"] == "task.result" {
				if frame["status"] == "running" {
					// Match the updated control plane: persist the interim
					// handoff without sending a terminal task-ID ACK.
					handoffs.Add(1)
					continue
				}
				if err := ws.WriteJSON(map[string]any{"type": "task.result.ack", "task_id": frame["task_id"]}); err != nil {
					serverErr <- err
					return
				}
				if frame["status"] == "failed" {
					result <- frame
					<-done
					return
				}
			}
		}
	})
	var token [32]byte
	if _, err := rand.Read(token[:]); err != nil {
		t.Fatal(err)
	}
	write("config/agent.env", []byte(fmt.Sprintf("HFL_DATA_DIR=%s\nHFL_AGENT_ROOT=%s\nHFL_KOPIA_PATH=%s/bin/kopia\nHFL_NODE_ROLE=agent\nHFL_NODE_ID=1\nHFL_NODE_TOKEN=hfln_%x\nHFL_ORG_KEY=isolated-test\nHFL_WSS_URL=%s/ws\n", root, root, root, token, strings.Replace(server.URL, "http://", "ws://", 1))), 0600)
	log, err := os.Create(filepath.Join(t.TempDir(), "legacy.log"))
	if err != nil {
		t.Fatal(err)
	}
	defer log.Close()
	cmd := exec.Command(filepath.Join(root, "bin/hfl-agent"), "run", "--data-dir", root)
	cmd.Stdout = log
	cmd.Stderr = log
	// No real control-plane endpoints or credentials enter this fixture.
	baseEnv := []string{}
	for _, pair := range os.Environ() {
		if !strings.HasPrefix(pair, "HFL_") && !strings.HasPrefix(pair, "KOPIA_") {
			baseEnv = append(baseEnv, pair)
		}
	}
	cmd.Env = append(baseEnv, "PATH="+filepath.Join(root, "bin")+":"+os.Getenv("PATH"), "HFL_DATA_DIR="+root, "HFL_AGENT_ROOT="+root, "HFL_INSTALLATION_MODE=system")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill(); _ = cmd.Wait() })
	select {
	case frame := <-result:
		failure, _ := frame["result"].(map[string]any)
		if failure["error_code"] != "AGENT_UPGRADE_ROLLBACK_FILES_UNVERIFIED" || failure["attempt_id"] == nil || failure["phase"] != "rollback_verification" || failure["rollback_phase"] != "rollback_failed" {
			details := ""
			for _, name := range []string{"install.log", "upgrade.log"} {
				raw, _ := os.ReadFile(filepath.Join(root, "logs", name))
				details += "\n" + name + "\n" + string(raw)
			}
			t.Fatalf("lost actual installer cause: %v%s", frame, details)
		}
		if err := cmd.Process.Signal(syscall.Signal(0)); err != nil {
			t.Fatalf("legacy process did not survive: %v", err)
		}
		t.Log("Unchanged Agent 0.3.12 delivered attempt-bound failure through the existing outbox on its original WebSocket.")
		if handoffs.Load() != 1 {
			t.Fatalf("expected one old-protocol running handoff without ACK, got %d", handoffs.Load())
		}
		if connections.Load() != 1 {
			t.Fatalf("result required a reconnect: %d", connections.Load())
		}
		runner, err := os.ReadFile(filepath.Join(root, "lifecycle/upgrade/run-upgrade.sh"))
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Contains(runner, []byte("Attempting service recovery")) {
			t.Fatal("legacy detached runner was not exercised")
		}
	case err := <-serverErr:
		_ = log.Sync()
		raw, _ := os.ReadFile(log.Name())
		t.Fatalf("legacy session failed: %v\n%s", err, raw)
	case <-time.After(95 * time.Second):
		_ = log.Sync()
		raw, _ := os.ReadFile(log.Name())
		t.Fatalf("legacy outbox did not report failure without reconnect\n%s", raw)
	}
}
