package kopialayout_test

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"hyperfilelens/agent/internal/infra/database"
	"hyperfilelens/agent/internal/infra/kopialayout"
	"hyperfilelens/agent/internal/model"
)

// This test uses only temporary local repositories, never development storage.
// Set HFL_TEST_KOPIA_BINARY to the verified bundled binary for the current OS.
func TestBundledKopiaMigrationAndWholeCacheDeletion(t *testing.T) {
	bin := os.Getenv("HFL_TEST_KOPIA_BINARY")
	if bin == "" {
		t.Skip("set HFL_TEST_KOPIA_BINARY to run the isolated real-Kopia regression")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	root := t.TempDir()
	logs := filepath.Join(root, "logs")
	identity := []byte("HFL_NODE_ID=isolated-node\n")
	if err := os.MkdirAll(filepath.Join(root, "config"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "config", "agent.env"), identity, 0o600); err != nil {
		t.Fatal(err)
	}
	db, err := database.Open(ctx, database.DefaultPath(root))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	aliases := database.NewRepoRepo(db)
	tasks := database.NewTaskRepo(db)
	if err := tasks.RecordCommand(ctx, database.RecordInput{
		TaskID: "recovery-fixture", Kind: "backup", Payload: map[string]any{"path": "preserved-source"},
	}); err != nil {
		t.Fatal(err)
	}
	type connection struct {
		old, next, cache, source, snapshot string
		content                            []byte
	}
	var connections []connection
	run := func(config string, env []string, args ...string) []byte {
		t.Helper()
		args = append([]string{"--config-file=" + config, "--no-progress"}, args...)
		cmd := exec.CommandContext(ctx, bin, args...)
		cmd.Env = append(os.Environ(), env...)
		var stderr bytes.Buffer
		cmd.Stderr = &stderr
		out, err := cmd.Output()
		if err != nil {
			t.Fatalf("isolated Kopia command failed: %v: %s", err, stderr.String())
		}
		return out
	}
	for _, name := range []string{"repo-1.config", "repo-2.config"} {
		c := connection{
			old:     filepath.Join(kopialayout.LegacyConfigDir(root), name),
			source:  filepath.Join(t.TempDir(), "source"),
			content: []byte("recoverable content for " + name),
		}
		c.next = kopialayout.ResolveConfig(root, c.old)
		c.cache = kopialayout.CacheDir(root, c.old)
		if err := os.MkdirAll(c.source, 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(c.source, "document.txt"), c.content, 0o600); err != nil {
			t.Fatal(err)
		}
		env := []string{
			"KOPIA_PASSWORD=isolated-integration-fixture", "KOPIA_CHECK_FOR_UPDATES=false",
			"KOPIA_PERSIST_CREDENTIALS_ON_CONNECT=false", "KOPIA_USE_KEYRING=false",
			"KOPIA_CACHE_DIRECTORY=" + c.cache, "KOPIA_LOG_DIR=" + filepath.Join(root, "cache", "kopia"),
		}
		run(c.old, env, "repository", "create", "filesystem", "--path="+filepath.Join(t.TempDir(), "repository"))
		var snapshot struct {
			ID string `json:"id"`
		}
		if err := json.Unmarshal(run(c.old, env, "snapshot", "create", "--json", c.source), &snapshot); err != nil || snapshot.ID == "" {
			t.Fatalf("valid backup was not created: %v", err)
		}
		c.snapshot = snapshot.ID
		if err := aliases.Connect(ctx, database.ConnectInput{Name: name, ConfigFile: c.old}); err != nil {
			t.Fatal(err)
		}
		connections = append(connections, c)
	}
	if connections[0].cache == connections[1].cache {
		t.Fatal("repository caches were shared")
	}
	if err := kopialayout.Migrate(ctx, root, logs, aliases.RelocateConfigPaths); err != nil {
		state, _ := os.ReadFile(kopialayout.StatePath(root))
		var inventory struct{ Retained []string }
		_ = json.Unmarshal(state, &inventory)
		t.Logf("isolated retained paths: %v", inventory.Retained)
		t.Fatal(err)
	}
	for _, c := range connections {
		alias, err := aliases.Get(ctx, filepath.Base(c.old))
		if err != nil || alias.ConfigFile != c.next {
			t.Fatal("durable repository alias did not migrate")
		}
		if _, err := os.Stat(filepath.Join(c.cache, "CACHEDIR.TAG")); err != nil {
			t.Fatal("warm cache was orphaned")
		}
	}
	if err := kopialayout.ClearCache(ctx, root); err != nil {
		t.Fatal(err)
	}
	for _, c := range connections {
		env, err := kopialayout.ProcessEnv(root, logs, map[string]string{
			"KOPIA_PASSWORD":        "isolated-integration-fixture",
			"KOPIA_CACHE_DIRECTORY": kopialayout.CacheDir(root, c.next),
		})
		if err != nil {
			t.Fatal(err)
		}
		var pairs []string
		for key, value := range env {
			pairs = append(pairs, key+"="+value)
		}
		run(c.next, pairs, "snapshot", "list", "--json")
		output := filepath.Join(t.TempDir(), "restore")
		run(c.next, pairs, "restore", c.snapshot, output)
		restored, err := os.ReadFile(filepath.Join(output, "document.txt"))
		if err != nil || !bytes.Equal(restored, c.content) {
			t.Fatal("existing backup could not be restored after cache deletion")
		}
	}
	gotIdentity, err := os.ReadFile(filepath.Join(root, "config", "agent.env"))
	if err != nil || !bytes.Equal(gotIdentity, identity) {
		t.Fatal("node identity changed")
	}
	task, err := tasks.Get(ctx, "recovery-fixture")
	if err != nil || task.Kind != "backup" || task.Status != model.TaskStatusRunning {
		t.Fatalf("durable task/recovery state changed: %v", err)
	}
	if err := kopialayout.Migrate(ctx, root, logs, aliases.RelocateConfigPaths); err != nil {
		state, _ := os.ReadFile(kopialayout.StatePath(root))
		var inventory struct{ Retained []string }
		_ = json.Unmarshal(state, &inventory)
		t.Logf("isolated retained paths after recreation: %v", inventory.Retained)
		t.Fatalf("repeated migration after cache recreation failed: %v", err)
	}
}
