package engine

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"hyperfilelens/agent/internal/model"
)

func TestDeleteManagedRepositoryPathRejectsTraversalAndRoot(t *testing.T) {
	root := t.TempDir()
	if _, _, err := deleteManagedRepositoryPath(context.Background(), root, root); err == nil {
		t.Fatal("expected cleanup root deletion to be rejected")
	}
	outside := filepath.Join(filepath.Dir(root), "outside")
	if _, _, err := deleteManagedRepositoryPath(context.Background(), outside, root); err == nil {
		t.Fatal("expected cleanup outside allowed root to be rejected")
	}
	if _, _, err := deleteManagedRepositoryPath(context.Background(), string(filepath.Separator), ""); err == nil {
		t.Fatal("expected filesystem root deletion to be rejected")
	}
}

func TestDeleteManagedRepositoryPathIsIdempotent(t *testing.T) {
	root := t.TempDir()
	target := filepath.Join(root, "hp-repos", "agent-1")
	if err := os.MkdirAll(target, 0o755); err != nil {
		t.Fatal(err)
	}
	if _, existed, err := deleteManagedRepositoryPath(context.Background(), target, root); err != nil || !existed {
		t.Fatalf("first cleanup existed=%v err=%v", existed, err)
	}
	if _, existed, err := deleteManagedRepositoryPath(context.Background(), target, root); err != nil || existed {
		t.Fatalf("second cleanup existed=%v err=%v", existed, err)
	}
}

func TestDeleteManagedRepositoryPathRejectsSymlink(t *testing.T) {
	root := t.TempDir()
	outside := t.TempDir()
	link := filepath.Join(root, "hp-repos")
	if err := os.Symlink(outside, link); err != nil {
		t.Skipf("symlink is unavailable: %v", err)
	}
	target := filepath.Join(link, "agent-1")
	if _, _, err := deleteManagedRepositoryPath(context.Background(), target, root); err == nil {
		t.Fatal("expected symbolic link component to be rejected")
	}
}

func TestDeleteManagedRepositoryPathHonorsCancellation(t *testing.T) {
	root := t.TempDir()
	target := filepath.Join(root, "hp-repos", "agent-1")
	if err := os.MkdirAll(target, 0o755); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, _, err := deleteManagedRepositoryPath(ctx, target, root); err == nil {
		t.Fatal("expected canceled cleanup to fail")
	}
	if _, err := os.Stat(target); err != nil {
		t.Fatalf("canceled cleanup removed target: %v", err)
	}
}

func TestRemoveRepositoryLocalStateReturnsCountWithoutPaths(t *testing.T) {
	configFile := filepath.Join(t.TempDir(), "repository.config")
	maintenanceFile := strings.TrimSuffix(configFile, filepath.Ext(configFile)) + ".maintenance.config"
	for _, path := range []string{configFile, maintenanceFile} {
		if err := os.WriteFile(path, []byte("test"), 0o600); err != nil {
			t.Fatal(err)
		}
	}

	removed, err := removeRepositoryLocalState(configFile)
	if err != nil {
		t.Fatal(err)
	}
	if removed != 2 {
		t.Fatalf("removed config count = %d, want 2", removed)
	}
}

func TestRepositoryCleanupConfigPathsIncludesLegacyNASConfig(t *testing.T) {
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: t.TempDir()}})
	paths := engine.repositoryCleanupConfigPaths(repositorySpec{
		ID:     50,
		Type:   "nas",
		Subdir: "hp-repos/agent-22",
	})

	if len(paths) != 2 {
		t.Fatalf("cleanup config paths = %v, want current and legacy paths", paths)
	}
	if filepath.Base(paths[0]) == filepath.Base(paths[1]) {
		t.Fatalf("cleanup paths must be distinct: %v", paths)
	}
	if filepath.Base(paths[1]) != "repo-50.config" {
		t.Fatalf("legacy config path = %q, want repo-50.config", paths[1])
	}
}

func TestManagedRepositoryCleanupRemovesOnlyOwnedCache(t *testing.T) {
	dataDir := t.TempDir()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: dataDir}})
	physicalRoot, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	firstSpec := repositorySpec{ID: 71, Type: "proxy_fs", Path: filepath.Join(physicalRoot, "repo-71")}
	secondSpec := repositorySpec{ID: 72, Type: "proxy_fs", Path: filepath.Join(physicalRoot, "repo-72")}
	for _, spec := range []repositorySpec{firstSpec, secondSpec} {
		if err := os.MkdirAll(spec.Path, 0o755); err != nil {
			t.Fatal(err)
		}
		configFile := engine.repositoryConfigPath(spec)
		if err := os.MkdirAll(filepath.Dir(configFile), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(configFile, []byte("config"), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.MkdirAll(managedRepositoryCacheDir(engine.current(), configFile), 0o755); err != nil {
			t.Fatal(err)
		}
	}

	payload := ParsePayload(map[string]any{
		"operation_type": "cleanup.repository",
		"repository": map[string]any{
			"id":   firstSpec.ID,
			"type": firstSpec.Type,
			"path": firstSpec.Path,
		},
	})
	status, result, message := engine.runManagedRepositoryCleanup(
		context.Background(), ReporterSink{}, "cleanup-71", payload,
	)
	if status != "success" {
		t.Fatalf("cleanup status=%q message=%q result=%#v", status, message, result)
	}
	if result["repository_cache_existed"] != true {
		t.Fatalf("cleanup did not report owned cache: %#v", result)
	}
	firstCache := managedRepositoryCacheDir(engine.current(), engine.repositoryConfigPath(firstSpec))
	if _, err := os.Stat(firstCache); !os.IsNotExist(err) {
		t.Fatalf("owned cache still exists: %v", err)
	}
	secondCache := managedRepositoryCacheDir(engine.current(), engine.repositoryConfigPath(secondSpec))
	if _, err := os.Stat(secondCache); err != nil {
		t.Fatalf("other repository cache was removed: %v", err)
	}
}

func TestRedactRepositoryCleanupPaths(t *testing.T) {
	path := filepath.Join(string(filepath.Separator), "sensitive", "repository")
	message := redactRepositoryCleanupPaths("remove "+path+": permission denied", path)
	if strings.Contains(message, path) {
		t.Fatalf("cleanup error leaked path: %q", message)
	}
}
