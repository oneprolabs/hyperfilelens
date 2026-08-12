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
	firstSpec := repositorySpec{
		ID: 71, Type: "proxy_fs", BasePath: physicalRoot,
		Path: filepath.Join(physicalRoot, "hfl-repo-71"), Layout: "managed_subdir_v1",
	}
	secondSpec := repositorySpec{
		ID: 72, Type: "proxy_fs", BasePath: physicalRoot,
		Path: filepath.Join(physicalRoot, "hfl-repo-72"), Layout: "managed_subdir_v1",
	}
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
			"id":        firstSpec.ID,
			"type":      firstSpec.Type,
			"path":      firstSpec.Path,
			"base_path": firstSpec.BasePath,
			"layout":    firstSpec.Layout,
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

func TestManagedNASCleanupSkipsUnmountedRemoteAndRemovesLocalState(t *testing.T) {
	dataDir := t.TempDir()
	t.Setenv("HFL_DATA_DIR", dataDir)
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: dataDir}})
	mountPoint := filepath.Join(dataDir, "mounts", "repositories", "repo-17-node-13")
	remoteLookingPath := filepath.Join(mountPoint, "hp-repos", "storage-17")
	if err := os.MkdirAll(remoteLookingPath, 0o755); err != nil {
		t.Fatal(err)
	}
	marker := filepath.Join(remoteLookingPath, "must-not-delete")
	if err := os.WriteFile(marker, []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}
	spec := repositorySpec{ID: 17, Type: "nas", Subdir: "hp-repos/storage-17"}
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

	payload := ParsePayload(map[string]any{
		"operation_type":   "cleanup.repository",
		"unmounted_policy": "retain_and_continue",
		"repository": map[string]any{
			"id": 17, "type": "nas", "subdir": spec.Subdir,
			"nas": map[string]any{
				"protocol": "smb", "server": "192.0.2.1", "share": "backup",
				"mount_point": mountPoint, "username": "backup", "password": "secret",
			},
		},
	})
	status, result, message := engine.runManagedRepositoryCleanup(
		context.Background(), ReporterSink{}, "cleanup-unmounted", payload,
	)
	if status != "success" {
		t.Fatalf("cleanup status=%q message=%q result=%#v", status, message, result)
	}
	if result["physical_cleanup"] != "skipped_unmounted" || result["cleanup_complete"] != false {
		t.Fatalf("unexpected cleanup result: %#v", result)
	}
	if _, err := os.Stat(marker); err != nil {
		t.Fatalf("unmounted mount-point contents were touched: %v", err)
	}
	if _, err := os.Stat(configFile); !os.IsNotExist(err) {
		t.Fatalf("local config still exists: %v", err)
	}
	if _, err := os.Stat(managedRepositoryCacheDir(engine.current(), configFile)); !os.IsNotExist(err) {
		t.Fatalf("local cache still exists: %v", err)
	}
}

func TestManagedProxyFSCleanupPreservesBaseDirectorySiblings(t *testing.T) {
	base := t.TempDir()
	target := filepath.Join(base, "hfl-repo-81")
	sibling := filepath.Join(base, "important-user-file")
	if err := os.MkdirAll(target, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(sibling, []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}

	if _, _, err := deleteManagedRepositoryPath(context.Background(), target, base); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(sibling); err != nil {
		t.Fatalf("base directory sibling was removed: %v", err)
	}
	if _, err := os.Stat(base); err != nil {
		t.Fatalf("base directory was removed: %v", err)
	}
}

func TestValidateManagedProxyFSRepositoryPathRejectsOwnershipMismatch(t *testing.T) {
	base := t.TempDir()
	for _, spec := range []repositorySpec{
		{ID: 91, Type: "proxy_fs", BasePath: base, Path: filepath.Join(base, "user-data")},
		{ID: 91, Type: "proxy_fs", BasePath: base, Path: filepath.Join(base, "hfl-repo-92"), Layout: "managed_subdir_v1"},
	} {
		if _, _, err := validateManagedProxyFSRepositoryPath(spec); err == nil {
			t.Fatalf("expected ownership validation failure for %#v", spec)
		}
	}
}

func TestManagedProxyFSCreatePathMustNotAlreadyExist(t *testing.T) {
	base := t.TempDir()
	spec := repositorySpec{
		ID: 92, Type: "proxy_fs", BasePath: base,
		Path: filepath.Join(base, "hfl-repo-92"), Layout: "managed_subdir_v1",
	}

	exists, err := managedProxyFSCreatePathExists(spec)
	if err != nil || exists {
		t.Fatalf("absent managed path exists=%v err=%v", exists, err)
	}
	if err := os.Mkdir(spec.Path, 0o755); err != nil {
		t.Fatal(err)
	}
	exists, err = managedProxyFSCreatePathExists(spec)
	if err != nil || !exists {
		t.Fatalf("existing managed path exists=%v err=%v", exists, err)
	}
}

func TestManagedRepositoryCleanupPreservesLegacyProxyFSDirectory(t *testing.T) {
	dataDir := t.TempDir()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: dataDir}})
	legacyPath := filepath.Join(t.TempDir(), "legacy-repository")
	if err := os.MkdirAll(legacyPath, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(legacyPath, "important"), []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}

	payload := ParsePayload(map[string]any{
		"operation_type": "cleanup.repository",
		"cleanup_scope":  "local_state_only",
		"repository": map[string]any{
			"id": 93, "type": "proxy_fs", "path": legacyPath,
		},
	})
	status, result, message := engine.runManagedRepositoryCleanup(
		context.Background(), ReporterSink{}, "cleanup-legacy", payload,
	)
	if status != "success" {
		t.Fatalf("cleanup status=%q message=%q result=%#v", status, message, result)
	}
	if result["physical_cleanup"] != "preserved_legacy_directory" {
		t.Fatalf("unexpected physical cleanup result: %#v", result)
	}
	if _, err := os.Stat(filepath.Join(legacyPath, "important")); err != nil {
		t.Fatalf("legacy repository data was removed: %v", err)
	}
}

func TestRedactRepositoryCleanupPaths(t *testing.T) {
	path := filepath.Join(string(filepath.Separator), "sensitive", "repository")
	message := redactRepositoryCleanupPaths("remove "+path+": permission denied", path)
	if strings.Contains(message, path) {
		t.Fatalf("cleanup error leaked path: %q", message)
	}
}
