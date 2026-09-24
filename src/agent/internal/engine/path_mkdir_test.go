package engine

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"hyperfilelens/agent/internal/model"
)

func TestRunPathMkdirCreatesSingleChildDirectory(t *testing.T) {
	parent := t.TempDir()
	target := filepath.Join(parent, "restore_test")

	result := New(nil).Run(context.Background(), Command{
		ID:   "path-mkdir-test",
		Kind: "path.mkdir",
		Payload: map[string]any{
			"path": target,
		},
	}, nil)

	if result.Status != "success" || result.Error != "" {
		t.Fatalf("status=%q error=%q result=%#v", result.Status, result.Error, result.Result)
	}
	if result.Result["path"] != target || result.Result["path_type"] != "directory" || result.Result["created"] != true {
		t.Fatalf("unexpected result: %#v", result.Result)
	}
	info, err := os.Stat(target)
	if err != nil || !info.IsDir() {
		t.Fatalf("created target is not a directory: info=%v err=%v", info, err)
	}
}

func TestRunPathMkdirRejectsExistingPathAndNestedName(t *testing.T) {
	parent := t.TempDir()
	target := filepath.Join(parent, "restore_test")
	if err := os.Mkdir(target, 0o755); err != nil {
		t.Fatal(err)
	}

	result := New(nil).Run(context.Background(), Command{
		ID:      "path-mkdir-existing",
		Kind:    "path.mkdir",
		Payload: map[string]any{"path": target},
	}, nil)
	if result.Status != "failed" || result.Result["error_code"] != "PATH_ALREADY_EXISTS" {
		t.Fatalf("existing target result=%#v", result)
	}

	result = New(nil).Run(context.Background(), Command{
		ID:      "path-mkdir-nested-name",
		Kind:    "path.mkdir",
		Payload: map[string]any{"path": filepath.Join(parent, "a", "b")},
	}, nil)
	if result.Status != "failed" || result.Result["error_code"] != "PATH_PARENT_NOT_FOUND" {
		t.Fatalf("nested target result=%#v", result)
	}
}

func TestRunPathMkdirRejectsProtectedAgentPath(t *testing.T) {
	root := t.TempDir()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		AgentRoot: root,
	}})
	result := engine.Run(context.Background(), Command{
		ID:   "path-mkdir-protected",
		Kind: "path.mkdir",
		Payload: map[string]any{
			"path": filepath.Join(root, "new"),
		},
	}, nil)
	if result.Status != "failed" || result.Result["error_code"] != agentPathForbiddenCode {
		t.Fatalf("protected target result=%#v error=%q", result.Result, result.Error)
	}
}

func TestRunPathMkdirPropagatesParentPermissionFailure(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root can bypass directory permissions")
	}
	parent := t.TempDir()
	if err := os.Chmod(parent, 0o500); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = os.Chmod(parent, 0o700) }()
	result := New(nil).Run(context.Background(), Command{
		ID:      "path-mkdir-permission",
		Kind:    "path.mkdir",
		Payload: map[string]any{"path": filepath.Join(parent, "new")},
	}, nil)
	if result.Status != "failed" || result.Result["error_code"] != pathPermissionDeniedErrorCode {
		t.Fatalf("permission result=%#v error=%q", result.Result, result.Error)
	}
}

func TestRunPathMkdirUsesStableMissingParentError(t *testing.T) {
	parent := filepath.Join(t.TempDir(), "missing")
	result := New(nil).Run(context.Background(), Command{
		ID:      "path-mkdir-missing-parent",
		Kind:    "path.mkdir",
		Payload: map[string]any{"path": filepath.Join(parent, "new")},
	}, nil)
	if result.Status != "failed" || result.Result["error_code"] != "PATH_PARENT_NOT_FOUND" {
		t.Fatalf("missing parent result=%#v error=%q", result.Result, result.Error)
	}
}
