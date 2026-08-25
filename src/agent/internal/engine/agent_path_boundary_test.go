package engine

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/vfs"
)

func TestBackupPathBoundaryRejectsAgentRootAndRepositoryMount(t *testing.T) {
	root := t.TempDir()
	cfg := &model.AgentConfig{AgentRoot: root}
	if _, err := newBackupPathBoundary(cfg, filepath.Join(root, "data"), false); err == nil {
		t.Fatal("expected a direct Agent root child to be rejected")
	}
	if _, err := newBackupPathBoundary(cfg, filepath.Join(root, "mounts", "repositories", "repo-1"), false); err == nil {
		t.Fatal("expected a repository mount to be rejected")
	}
	repository := filepath.Join(root, "mounts", "repositories", "repo-1")
	if _, err := newBackupPathBoundary(cfg, repository, true, repository); err == nil {
		t.Fatal("expected a task payload to be unable to authorize a repository mount")
	}
}

func TestBackupPathBoundaryAllowsBoundNASSource(t *testing.T) {
	root := t.TempDir()
	cfg := &model.AgentConfig{AgentRoot: root}
	mount := filepath.Join(root, "mounts", "sources", "source-7")
	b, err := newBackupPathBoundary(cfg, filepath.Join(mount, "documents"), true, mount)
	if err != nil {
		t.Fatalf("expected bound NAS source to be allowed: %v", err)
	}
	if len(b.patterns()) != 0 || len(b.exclusions()) != 0 {
		t.Fatalf("NAS source should not carry host exclusions: %#v", b)
	}
	if _, err := newBackupPathBoundary(cfg, mount, true); err == nil {
		t.Fatal("expected NAS source without an explicit bound mount to be rejected")
	}
	otherMount := filepath.Join(root, "mounts", "sources", "source-8")
	if _, err := newBackupPathBoundary(cfg, otherMount, true, mount); err == nil {
		t.Fatal("expected a different task-bound NAS source to be rejected")
	}
}

func TestBackupPathBoundaryAllowsTaskBoundCustomNASSource(t *testing.T) {
	root := t.TempDir()
	mount := filepath.Join(root, "mounts", "custom", "team-nas")
	if _, err := newBackupPathBoundary(
		&model.AgentConfig{AgentRoot: root},
		filepath.Join(mount, "documents"),
		true,
		mount,
	); err != nil {
		t.Fatalf("expected a task-bound custom NAS source mount to be allowed: %v", err)
	}
}

func TestBackupPathBoundaryExcludesAgentRootWhenBackingUpParent(t *testing.T) {
	root := t.TempDir()
	parent := filepath.Dir(root)
	b, err := newBackupPathBoundary(&model.AgentConfig{AgentRoot: root}, parent, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(b.patterns()) != 1 || len(b.exclusions()) != 1 {
		t.Fatalf("expected one system boundary: %#v", b)
	}
	if runtime.GOOS != "windows" && b.patterns()[0] != filepath.Base(root) {
		t.Fatalf("unexpected relative ignore pattern: %#v", b.patterns())
	}
}

func TestBackupPathBoundaryAcceptsAgentRootNameStartingWithDots(t *testing.T) {
	parent := t.TempDir()
	root := filepath.Join(parent, "..agent-state", "hyperfilelens-agent")
	b, err := newBackupPathBoundary(&model.AgentConfig{AgentRoot: root}, parent, false)
	if err != nil {
		t.Fatal(err)
	}
	want := normalizeIgnorePattern(filepath.Join("..agent-state", "hyperfilelens-agent"))
	if len(b.patterns()) != 1 || b.patterns()[0] != want {
		t.Fatalf("relative Agent root pattern = %#v, want %q", b.patterns(), want)
	}
}

func TestBackupPathBoundaryUsesRootForEveryInstallationMode(t *testing.T) {
	modes := []model.InstallationMode{
		model.InstallationModeSystem,
		model.InstallationModeAccount,
		model.InstallationModeUser,
		model.InstallationModeUserContinuous,
	}
	for _, mode := range modes {
		t.Run(string(mode), func(t *testing.T) {
			configuredRoot := filepath.Join(t.TempDir(), "agent-root")
			b, err := newBackupPathBoundary(
				&model.AgentConfig{InstallationMode: mode, AgentRoot: configuredRoot},
				t.TempDir(),
				false,
			)
			if err != nil {
				t.Fatal(err)
			}
			want, err := canonicalPath(configuredRoot)
			if err != nil {
				t.Fatal(err)
			}
			if b.agentRoot != want {
				t.Fatalf("Agent root = %q, want %q", b.agentRoot, want)
			}
		})
	}
}

func TestBackupPathBoundaryFallsBackByInstallationMode(t *testing.T) {
	for _, mode := range []model.InstallationMode{
		model.InstallationModeSystem,
		model.InstallationModeAccount,
		model.InstallationModeUser,
		model.InstallationModeUserContinuous,
	} {
		t.Run(string(mode), func(t *testing.T) {
			b, err := newBackupPathBoundary(
				&model.AgentConfig{InstallationMode: mode},
				t.TempDir(),
				false,
			)
			if err != nil {
				t.Fatal(err)
			}
			want, err := canonicalPath(vfs.AgentRootForMode(mode))
			if err != nil {
				t.Fatal(err)
			}
			if b.agentRoot != want {
				t.Fatalf("fallback Agent root = %q, want %q", b.agentRoot, want)
			}
		})
	}
}

func TestBackupPathBoundaryRejectsSymlinkIntoAgentRoot(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("creating symlinks requires elevated privileges on Windows")
	}
	root := t.TempDir()
	linkParent := t.TempDir()
	link := filepath.Join(linkParent, "agent-link")
	if err := symlinkForTest(root, link); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if _, err := newBackupPathBoundary(&model.AgentConfig{AgentRoot: root}, filepath.Join(link, "data"), false); err == nil {
		t.Fatal("expected symlink into Agent root to be rejected")
	}
}

func TestRunPathInfoRejectsAgentInternalDirectory(t *testing.T) {
	root := t.TempDir()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		AgentRoot:        root,
		InstallationMode: model.InstallationModeSystem,
	}})
	status, result, _ := engine.runPathInfo(context.Background(), Payload{Path: root, Extra: map[string]any{}})
	if status != "failed" || result["error_code"] != agentPathForbiddenCode {
		t.Fatalf("expected protected path failure, status=%q result=%#v", status, result)
	}
}

func TestRunBrowseMarksAgentRootUnselectable(t *testing.T) {
	parent := t.TempDir()
	root := filepath.Join(parent, "agent-root")
	if err := os.Mkdir(root, 0o700); err != nil {
		t.Fatal(err)
	}
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		AgentRoot:        root,
		InstallationMode: model.InstallationModeSystem,
	}})
	status, result, errMessage := engine.runBrowse(
		context.Background(), ReporterSink{}, "browse-agent-root",
		Payload{Path: parent, Extra: map[string]any{}, DirsOnly: true},
	)
	if status != "success" {
		t.Fatalf("browse failed: %s", errMessage)
	}
	rows, ok := result["entries"].([]map[string]any)
	if !ok {
		t.Fatalf("unexpected browse entries: %#v", result["entries"])
	}
	for _, row := range rows {
		if row["path"] != root {
			continue
		}
		if row["selectable"] != false || row["protection_reason"] != "agent_internal_root" {
			t.Fatalf("Agent root was not protected: %#v", row)
		}
		return
	}
	t.Fatalf("Agent root entry not returned: %#v", rows)
}

func TestRunBrowseRejectsAgentInternalDirectory(t *testing.T) {
	root := t.TempDir()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		AgentRoot:        root,
		InstallationMode: model.InstallationModeSystem,
	}})
	status, result, _ := engine.runBrowse(
		context.Background(), ReporterSink{}, "browse-inside-agent-root",
		Payload{Path: root, Extra: map[string]any{}, DirsOnly: true},
	)
	if status != "failed" || result["error_code"] != agentPathForbiddenCode {
		t.Fatalf("expected protected browse failure, status=%q result=%#v", status, result)
	}
}

func symlinkForTest(target, link string) error {
	return os.Symlink(target, link)
}
