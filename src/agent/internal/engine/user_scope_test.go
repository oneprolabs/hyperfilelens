package engine

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"hyperfilelens/agent/internal/model"
)

func TestUserInstallationScopeDefaultsBrowseToHome(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("test uses Unix HOME semantics")
	}
	home := t.TempDir()
	t.Setenv("HOME", home)
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		InstallationMode: model.InstallationModeUser,
	}})

	payload, err := engine.applyUserInstallationScope(
		"browse",
		Payload{ListMounts: true, Extra: map[string]any{}},
	)
	if err != nil {
		t.Fatal(err)
	}
	if payload.Path != home || payload.ListMounts {
		t.Fatalf("browse scope = path %q, list_mounts=%t", payload.Path, payload.ListMounts)
	}
}

func TestUserInstallationScopeReturnsStructuredPermissionFailure(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("test uses Unix HOME semantics")
	}
	root := t.TempDir()
	home := filepath.Join(root, "home")
	outside := filepath.Join(root, "outside")
	for _, path := range []string{home, outside} {
		if err := os.Mkdir(path, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("HOME", home)
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		InstallationMode: model.InstallationModeUser,
	}})

	result := engine.Run(context.Background(), Command{
		Kind: "path.info",
		Payload: map[string]any{
			"path": outside,
		},
	}, nil)

	if result.Status != "failed" {
		t.Fatalf("status = %q, want failed", result.Status)
	}
	if result.Result["error_code"] != pathPermissionDeniedErrorCode {
		t.Fatalf("error_code = %#v", result.Result["error_code"])
	}
}

func TestUserInstallationScopeRejectsOutsideBackupAndAllowsHomeRestore(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("test uses Unix HOME semantics")
	}
	root := t.TempDir()
	home := filepath.Join(root, "home")
	outside := filepath.Join(root, "outside")
	if err := os.Mkdir(home, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(outside, 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		InstallationMode: model.InstallationModeUser,
	}})
	managedRepository := map[string]any{
		"type":   "s3",
		"bucket": "backup-bucket",
	}

	if _, err := engine.applyUserInstallationScope(
		"backup",
		Payload{Path: outside, Extra: map[string]any{"repository": managedRepository}},
	); err == nil {
		t.Fatal("backup outside Home should be rejected")
	}

	target := filepath.Join(home, "restore", "document.txt")
	payload, err := engine.applyUserInstallationScope(
		"restore",
		Payload{Extra: map[string]any{
			"target_path": target,
			"repository":  managedRepository,
		}},
	)
	if err != nil {
		t.Fatal(err)
	}
	if payload.Extra["target_path"] != target {
		t.Fatalf("restore target = %v", payload.Extra["target_path"])
	}
}

func TestUserInstallationScopeRejectsInfrastructureTasks(t *testing.T) {
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		InstallationMode: model.InstallationModeUser,
	}})

	for _, kind := range []string{
		"nas.mount",
		"lens.workspace.prepare",
		"repository.server.start",
	} {
		t.Run(kind, func(t *testing.T) {
			if _, err := engine.applyUserInstallationScope(
				kind,
				Payload{Extra: map[string]any{}},
			); err == nil {
				t.Fatalf("%s should be rejected for a user-level Agent", kind)
			}
		})
	}
}

func TestUserInstallationScopeDefaultsToDenyForUnknownTasks(t *testing.T) {
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		InstallationMode: model.InstallationModeUser,
	}})

	if _, err := engine.applyUserInstallationScope(
		"future.host.command",
		Payload{Extra: map[string]any{}},
	); err == nil {
		t.Fatal("unknown task kinds must be rejected for a user-level Agent")
	}
}

func TestUserInstallationScopeAllowsLifecycleAndManagedSnapshotTasks(t *testing.T) {
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		InstallationMode: model.InstallationModeUser,
	}})
	managedPayload := Payload{Extra: map[string]any{
		"repository": map[string]any{"type": "s3", "bucket": "backup-bucket"},
	}}

	for _, kind := range []string{
		"agent.ping",
		"agent.version",
		"agent.upgrade",
		"agent.uninstall",
		"task.cancel",
	} {
		t.Run(kind, func(t *testing.T) {
			if _, err := engine.applyUserInstallationScope(
				kind,
				Payload{Extra: map[string]any{}},
			); err != nil {
				t.Fatalf("%s should remain available: %v", kind, err)
			}
		})
	}
	for _, kind := range []string{"snapshot.browse", "snapshot.download", "snapshot.delete"} {
		t.Run(kind, func(t *testing.T) {
			if _, err := engine.applyUserInstallationScope(kind, managedPayload); err != nil {
				t.Fatalf("managed %s should remain available: %v", kind, err)
			}
			if _, err := engine.applyUserInstallationScope(
				kind,
				Payload{Extra: map[string]any{}},
			); err == nil {
				t.Fatalf("unmanaged %s should be rejected", kind)
			}
		})
	}
}

func TestUserInstallationScopeRejectsNestedNASRepository(t *testing.T) {
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		InstallationMode: model.InstallationModeUser,
	}})

	for _, kind := range []string{
		"backup",
		"restore",
		"repo.status",
		"repository.operation",
	} {
		t.Run(kind, func(t *testing.T) {
			_, err := engine.applyUserInstallationScope(
				kind,
				Payload{Extra: map[string]any{
					"repository": map[string]any{
						"type": "NAS",
						"nas":  map[string]any{"host": "storage.example"},
					},
				}},
			)
			if err == nil {
				t.Fatalf("%s should reject a nested NAS repository", kind)
			}
		})
	}
}

func TestUserInstallationScopeRejectsLocalFilesystemRepository(t *testing.T) {
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		InstallationMode: model.InstallationModeUser,
	}})

	for _, kind := range []string{
		"repo.initialize",
		"repo.status",
		"repository.operation",
	} {
		t.Run(kind, func(t *testing.T) {
			_, err := engine.applyUserInstallationScope(
				kind,
				Payload{Extra: map[string]any{
					"repository": map[string]any{
						"type": "proxy_fs",
						"path": "/tmp/repository",
					},
				}},
			)
			if err == nil {
				t.Fatalf("%s should reject a local filesystem repository", kind)
			}
		})
	}
}

func TestUserInstallationScopeRejectsExternalRepositoryConfig(t *testing.T) {
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		InstallationMode: model.InstallationModeUser,
	}})

	for name, payload := range map[string]Payload{
		"top-level": {
			ConfigFile: "/tmp/external.config",
			Extra:      map[string]any{},
		},
		"managed-repository": {
			Extra: map[string]any{
				"repository": map[string]any{
					"type":        "s3",
					"config_file": "/tmp/external.config",
				},
			},
		},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := engine.applyUserInstallationScope("repo.status", payload); err == nil {
				t.Fatal("external repository config should be rejected")
			}
		})
	}
}

func TestUserInstallationScopeRejectsRawArgumentsAndCustomEnvironment(t *testing.T) {
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		InstallationMode: model.InstallationModeUser,
	}})
	managedRepository := map[string]any{
		"type":   "s3",
		"bucket": "backup-bucket",
	}
	for name, payload := range map[string]Payload{
		"known task raw arguments": {
			Path:  "/tmp/source",
			Args:  []string{"repository", "status"},
			Extra: map[string]any{"repository": managedRepository},
		},
		"custom environment": {
			Extra: map[string]any{"repository": managedRepository},
			Env:   map[string]string{"KOPIA_CACHE_DIRECTORY": "/tmp/external-cache"},
		},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := engine.applyUserInstallationScope("repo.status", payload); err == nil {
				t.Fatal("user-level task override should be rejected")
			}
		})
	}
}

func TestUserInstallationScopeRequiresManagedRepository(t *testing.T) {
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		InstallationMode: model.InstallationModeUser,
	}})
	for _, test := range []struct {
		kind    string
		payload Payload
	}{
		{kind: "backup", payload: Payload{Path: "/tmp/source", Extra: map[string]any{}}},
		{kind: "restore", payload: Payload{Path: "/tmp/restore", Extra: map[string]any{}}},
		{kind: "repo.status", payload: Payload{Extra: map[string]any{}}},
		{kind: "snapshot.list", payload: Payload{Extra: map[string]any{}}},
	} {
		t.Run(test.kind, func(t *testing.T) {
			if _, err := engine.applyUserInstallationScope(test.kind, test.payload); err == nil {
				t.Fatalf("%s without a managed repository should be rejected", test.kind)
			}
		})
	}
}

func TestUserInstallationScopeAllowsManagedRemoteRepository(t *testing.T) {
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		InstallationMode: model.InstallationModeUser,
	}})
	for _, repositoryType := range []string{"s3", "kopia_server"} {
		t.Run(repositoryType, func(t *testing.T) {
			_, err := engine.applyUserInstallationScope(
				"repo.status",
				Payload{Extra: map[string]any{
					"repository": map[string]any{"type": repositoryType},
				}},
			)
			if err != nil {
				t.Fatalf("managed %s repository rejected: %v", repositoryType, err)
			}
		})
	}
}
