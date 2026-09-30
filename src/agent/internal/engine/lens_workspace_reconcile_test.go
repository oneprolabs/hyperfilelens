package engine

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"hyperfilelens/agent/internal/model"
)

func TestReconcileSnapshotWorkspaceRetainsSidecarsAndPrunesDeletedSources(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{
		"unchanged.docx", "changed.docx", "deleted.docx",
		"unchanged.docx.sourcelens/content.md",
		"changed.docx.sourcelens/content.md",
		"deleted.docx.sourcelens/content.md",
		".sourcelens-datasource.json",
	} {
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("content"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	list := func(_ context.Context, rel string) ([]snapshotWorkspaceEntry, error) {
		if rel != "" {
			t.Fatalf("unexpected directory: %q", rel)
		}
		return []snapshotWorkspaceEntry{
			{name: "unchanged.docx"}, {name: "changed.docx"},
		}, nil
	}
	removed, err := reconcileSnapshotWorkspaceWithMarker(context.Background(), root, list, true)
	if err != nil || removed != 2 {
		t.Fatalf("first reconcile: removed=%d err=%v", removed, err)
	}
	for _, name := range []string{
		"unchanged.docx.sourcelens/content.md",
		"changed.docx.sourcelens/content.md",
		".sourcelens-datasource.json",
	} {
		if _, err := os.Stat(filepath.Join(root, name)); err != nil {
			t.Fatalf("retained %s: %v", name, err)
		}
	}
	for _, name := range []string{"deleted.docx", "deleted.docx.sourcelens"} {
		if _, err := os.Stat(filepath.Join(root, name)); !os.IsNotExist(err) {
			t.Fatalf("deleted %s still exists: %v", name, err)
		}
	}
	removed, err = reconcileSnapshotWorkspaceWithMarker(context.Background(), root, list, true)
	if err != nil || removed != 0 {
		t.Fatalf("idempotent retry: removed=%d err=%v", removed, err)
	}
}

func TestReconcileSnapshotWorkspaceFailsClosedOnUnverifiedListing(t *testing.T) {
	root := t.TempDir()
	oldFile := filepath.Join(root, "old.txt")
	if err := os.WriteFile(oldFile, []byte("old"), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err := reconcileSnapshotWorkspace(context.Background(), root,
		func(_ context.Context, _ string) ([]snapshotWorkspaceEntry, error) {
			return []snapshotWorkspaceEntry{{name: "../outside"}}, nil
		},
	)
	if err == nil {
		t.Fatal("expected invalid snapshot entry to fail")
	}
	if _, err := os.Stat(oldFile); err != nil {
		t.Fatalf("file removed without a trustworthy listing: %v", err)
	}
}

func TestReconcileSnapshotWorkspaceDoesNotSilentlyRetainForeignMarker(t *testing.T) {
	root := t.TempDir()
	marker := filepath.Join(root, ".sourcelens-datasource.json")
	if err := os.WriteFile(marker, []byte("source-owned"), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err := reconcileSnapshotWorkspace(
		context.Background(), root,
		func(_ context.Context, _ string) ([]snapshotWorkspaceEntry, error) {
			return nil, nil
		},
	)
	if err == nil {
		t.Fatal("a marker outside the owned datasource root cannot be silently retained")
	}
	if _, err := os.Stat(marker); err != nil {
		t.Fatalf("foreign marker was removed: %v", err)
	}
}

func TestReconcileSnapshotWorkspaceOnlyPrunesWithinSelectedDirectory(t *testing.T) {
	workspace := t.TempDir()
	selected := filepath.Join(workspace, "reports")
	other := filepath.Join(workspace, "other")
	for _, dir := range []string{selected, other, filepath.Join(selected, "obsolete", "nested")} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	for _, path := range []string{
		filepath.Join(selected, "gone.txt"),
		filepath.Join(selected, "obsolete", "nested", "gone.txt"),
		filepath.Join(other, "keep.txt"),
	} {
		if err := os.WriteFile(path, []byte("data"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	removed, err := reconcileSnapshotWorkspace(
		context.Background(),
		selected,
		func(_ context.Context, _ string) ([]snapshotWorkspaceEntry, error) {
			return nil, nil
		},
	)
	if err != nil || removed != 2 {
		t.Fatalf("scope reconciliation: removed=%d err=%v", removed, err)
	}
	if _, err := os.Stat(filepath.Join(selected, "obsolete")); !os.IsNotExist(err) {
		t.Fatalf("stale nested directory remains: %v", err)
	}
	if _, err := os.Stat(filepath.Join(other, "keep.txt")); err != nil {
		t.Fatalf("another selected scope was removed: %v", err)
	}
}

func TestReconcileSnapshotWorkspaceRefusesSymlinkRoot(t *testing.T) {
	target := t.TempDir()
	link := filepath.Join(t.TempDir(), "workspace")
	if err := os.Symlink(target, link); err != nil {
		t.Fatal(err)
	}
	_, err := reconcileSnapshotWorkspace(
		context.Background(), link,
		func(_ context.Context, _ string) ([]snapshotWorkspaceEntry, error) {
			t.Fatal("snapshot must not be read when root is a symlink")
			return nil, nil
		},
	)
	if err == nil {
		t.Fatal("symlink root must be rejected")
	}
}

func TestChatUpdateDiskHeadroomRejectsInsufficientCapacity(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("the managed Data Gateway workspace requires Linux")
	}
	target := t.TempDir()
	if err := os.WriteFile(filepath.Join(target, "old.txt"), []byte("old"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := checkChatUpdateDiskHeadroom(context.Background(), target, 3); err != nil {
		t.Fatalf("unchanged content should pass the lower-bound check: %v", err)
	}
	if err := checkChatUpdateDiskHeadroom(context.Background(), target, int64(^uint64(0)>>1)); err == nil {
		t.Fatal("snapshot too large for the filesystem was accepted")
	}
}

func TestChatUpdateRestoreReconcilesWithSameKopiaConnection(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("managed workspace identity and fake Kopia require Linux")
	}
	root := newLensTestRoot(t)
	workspace := filepath.Join(root, "hfl-ks-"+testWorkspaceUID)
	eng := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir: filepath.Join(filepath.Dir(root), "agent"),
	}})
	identity := map[string]any{
		"workspace_root":         root,
		"workspace_uid":          testWorkspaceUID,
		"tenant_organization_id": 61,
		"gateway_link_id":        7,
		"knowledge_source_id":    42,
		"workspace_kind":         "managed_restore",
	}
	if status, _, errMsg := eng.runLensKsPrepare(context.Background(), ParsePayload(
		map[string]any{"path": workspace, "workspace_root": root,
			"workspace_uid": testWorkspaceUID, "tenant_organization_id": 61,
			"gateway_link_id": 7, "knowledge_source_id": 42,
			"workspace_kind": "managed_restore"},
	)); status != "success" {
		t.Fatalf("workspace preparation failed: %s", errMsg)
	}
	target := filepath.Join(workspace, "reports")
	if err := os.MkdirAll(filepath.Join(target, "retained.txt.sourcelens"), 0o700); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{
		filepath.Join(target, "retained.txt"),
		filepath.Join(target, "deleted.txt"),
		filepath.Join(target, "retained.txt.sourcelens", "content.md"),
	} {
		if err := os.WriteFile(path, []byte("content"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	commandLog := filepath.Join(filepath.Dir(root), "commands.log")
	kopiaBin := filepath.Join(filepath.Dir(root), "fake-kopia")
	script := "#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + commandLog + "\n" +
		"case \"$*\" in\n" +
		"  *'ls --hfl-summary'*) echo '{\"version\":1,\"path_type\":\"directory\",\"size_bytes\":7,\"file_count\":1,\"directory_count\":1,\"symlink_count\":0,\"summary_available\":true,\"complete\":true}'; exit 0 ;;\n" +
		"  *'ls -l'*) echo '-rw-r--r-- 7 2026-09-29 00:00:00 UTC object-1 retained.txt'; exit 0 ;;\n" +
		"  *'--progress restore'*) echo 'Restored 1 files, 0 directories and 0 symbolic links (7 B).' >&2; exit 0 ;;\n" +
		"esac\nexit 0\n"
	if err := os.WriteFile(kopiaBin, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	eng = New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir: filepath.Join(filepath.Dir(root), "agent"), KopiaPath: kopiaBin,
	}})
	payload := managedRestoreTestPayload(filepath.Dir(root), target, "directory", "overwrite")
	payload.Extra["managed_workspace_path"] = workspace
	payload.Extra["insight_content_policy"] = insightRegularFilesOnlyPolicy
	payload.Extra["chat_data_update_reconcile"] = true
	for key, value := range identity {
		payload.Extra[key] = value
	}
	status, _, message := eng.runManagedRestore(context.Background(), ReporterSink{}, "chat-update", payload)
	if status != "success" {
		t.Fatalf("restore and reconcile failed: %s", message)
	}
	if _, err := os.Stat(filepath.Join(target, "deleted.txt")); !os.IsNotExist(err) {
		t.Fatalf("stale file remains: %v", err)
	}
	if _, err := os.Stat(filepath.Join(target, "retained.txt.sourcelens", "content.md")); err != nil {
		t.Fatalf("successful conversion sidecar was deleted: %v", err)
	}
	commands, err := os.ReadFile(commandLog)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(commands), "--write-files-atomically") ||
		!strings.Contains(string(commands), "ls -l") {
		t.Fatalf("restore did not perform atomic write and snapshot listing: %s", commands)
	}
}
