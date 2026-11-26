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

	"hyperfilelens/agent/internal/infra/kopialayout"
)

func TestBundledKopiaDemandCacheRelocationAndRestore(t *testing.T) {
	bin := os.Getenv("HFL_TEST_KOPIA_BINARY")
	if bin == "" {
		t.Skip("set HFL_TEST_KOPIA_BINARY for isolated patched-Kopia validation")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	if !kopialayout.SupportsDemandDriven(ctx, bin) {
		t.Fatal("actual patched binary capability was not recognized")
	}
	base, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	root := filepath.Join(base, "agent")
	logs := filepath.Join(root, "logs")
	parent := filepath.Join(base, "destination")
	source := filepath.Join(base, "source")
	repository := filepath.Join(base, "repository")
	for _, dir := range []string{root, parent, source, kopialayout.ConfigDir(root)} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	if err := kopialayout.Migrate(ctx, root, logs, nil); err != nil {
		t.Fatal(err)
	}
	if err := kopialayout.ApplyPolicy(ctx, root, kopialayout.Policy{Version: 1, Revision: 1, Blocking: 1, Warning: 10*kopialayout.GIB + 1}); err != nil {
		t.Fatal(err)
	}
	config := filepath.Join(kopialayout.ConfigDir(root), "repo-1.config")
	run := func(args ...string) []byte {
		t.Helper()
		env, err := kopialayout.ProcessEnv(root, logs, map[string]string{"KOPIA_CACHE_DIRECTORY": kopialayout.CacheDir(root, config)})
		if err != nil {
			t.Fatal(err)
		}
		cmd := exec.CommandContext(ctx, bin, append([]string{"--config-file=" + config, "--no-progress"}, args...)...)
		cmd.Env = append(os.Environ(), "KOPIA_PASSWORD=isolated-cache-test", "KOPIA_USE_KEYRING=false", "KOPIA_PERSIST_CREDENTIALS_ON_CONNECT=false", "KOPIA_CHECK_FOR_UPDATES=false")
		for key, value := range env {
			cmd.Env = append(cmd.Env, key+"="+value)
		}
		var stderr bytes.Buffer
		cmd.Stderr = &stderr
		output, err := cmd.Output()
		if err != nil {
			t.Fatalf("isolated Kopia command failed: %v: %s", err, stderr.String())
		}
		return output
	}
	run("repository", "create", "filesystem", "--path="+repository, "--cache-demand-driven", "--content-cache-size-mb=0", "--metadata-cache-size-mb=0")
	content := bytes.Repeat([]byte("cache relocation must preserve valid backups\n"), 2000)
	if err := os.WriteFile(filepath.Join(source, "document.txt"), content, 0o600); err != nil {
		t.Fatal(err)
	}
	var snapshot struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(run("snapshot", "create", "--json", source), &snapshot); err != nil || snapshot.ID == "" {
		t.Fatalf("snapshot missing: %v", err)
	}
	run("snapshot", "restore", snapshot.ID, filepath.Join(base, "restore-before"))
	files := 0
	_ = filepath.WalkDir(filepath.Join(kopialayout.CacheDir(root, config), "contents"), func(_ string, d os.DirEntry, err error) error {
		if err == nil && d.Type().IsRegular() {
			files++
		}
		return nil
	})
	if files == 0 {
		t.Fatal("ordinary content caching was disabled by zero legacy budgets")
	}
	v, err := kopialayout.ValidateDestination(ctx, root, parent, logs)
	if err != nil {
		t.Fatal(err)
	}
	result, err := kopialayout.Relocate(ctx, root, logs, "real-relocation", v.Token, nil, func(path string) error { return kopialayout.VerifyCacheConnection(ctx, root, path) })
	if err != nil || result["stage"] != "completed" {
		t.Fatalf("relocation: %+v %v", result, err)
	}
	output := filepath.Join(base, "restore-after")
	run("snapshot", "restore", snapshot.ID, output)
	restored, err := os.ReadFile(filepath.Join(output, "document.txt"))
	if err != nil || !bytes.Equal(restored, content) {
		t.Fatal("existing backup did not restore after relocation")
	}
	// This explicit deletion is limited to the disposable test installation's
	// owned cache root, proving state remains independent and reconstruction works.
	if err := os.RemoveAll(v.Destination); err != nil {
		t.Fatal(err)
	}
	run("snapshot", "list", "--json")
	run("snapshot", "restore", snapshot.ID, filepath.Join(base, "restore-recreated"))
	if _, err := os.Stat(config); err != nil {
		t.Fatal("persistent connection lost after cache deletion")
	}
	// Reproduce the historical default-cache skeleton left behind on macOS.
	// It has no owner marker and must remain untouched until verified cutover.
	if err := os.MkdirAll(kopialayout.LegacyConfigDir(root), 0o700); err != nil {
		t.Fatal(err)
	}
	back, err := kopialayout.ValidateDestination(ctx, root, root, logs)
	if err != nil {
		t.Fatal(err)
	}
	if back.Destination != filepath.Join(root, "cache") {
		t.Fatal("default restore did not select canonical cache")
	}
	if _, err := kopialayout.Relocate(ctx, root, logs, "real-restore-default", back.Token, nil, func(path string) error { return kopialayout.VerifyCacheConnection(ctx, root, path) }); err != nil {
		t.Fatal(err)
	}
	run("snapshot", "restore", snapshot.ID, filepath.Join(base, "restore-after-default"))
	restoredDefault, err := os.ReadFile(filepath.Join(base, "restore-after-default", "document.txt"))
	if err != nil || !bytes.Equal(restoredDefault, content) {
		t.Fatal("backup no longer restores after returning to default cache")
	}

}
