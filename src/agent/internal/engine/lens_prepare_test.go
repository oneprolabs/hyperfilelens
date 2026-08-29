package engine

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

const testWorkspaceUID = "de240f46-eccd-4e4b-868f-b1f504fbe67b"

func newLensTestRoot(t *testing.T) string {
	t.Helper()
	root := filepath.Join(t.TempDir(), "data")
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatal(err)
	}
	return root
}

func testIdentityPath(root, workspaceUID string) string {
	return filepath.Join(filepath.Dir(root), ".hyperfilelens", "identities", workspaceUID+".json")
}

func testTrashPath(root, workspaceUID string) string {
	return filepath.Join(root, ".hyperfilelens-trash", workspaceUID)
}

func testTombstonePath(root, workspaceUID string) string {
	return filepath.Join(filepath.Dir(root), ".hyperfilelens", "tombstones", workspaceUID+".json")
}

func TestLensWorkspaceTrashStaysInsideWorkspaceFilesystem(t *testing.T) {
	root := newLensTestRoot(t)
	paths, err := resolveLensWorkspacePaths(
		filepath.Join(root, "tenants", "61", "knowledge-sources", "workspace-42"),
		root,
		testWorkspaceUID,
	)
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Dir(paths.TrashRoot) != root {
		t.Fatalf("trash root must stay inside workspace root: %s", paths.TrashRoot)
	}
}

func TestRunLensKsPrepareCreatesDirectory(t *testing.T) {
	root := newLensTestRoot(t)
	target := filepath.Join(root, "ks-42")
	eng := New(nil)

	status, result, errMsg := eng.runLensKsPrepare(context.Background(), Payload{
		Path: target,
		Extra: map[string]any{
			"workspace_root":         root,
			"workspace_uid":          testWorkspaceUID,
			"tenant_organization_id": 61,
			"gateway_link_id":        7,
			"knowledge_source_id":    42,
			"workspace_kind":         "managed_restore",
		},
	})
	if status != "success" {
		t.Fatalf("status=%q err=%q result=%v", status, errMsg, result)
	}
	info, err := os.Stat(target)
	if err != nil {
		t.Fatalf("stat: %v", err)
	}
	if !info.IsDir() {
		t.Fatalf("expected directory")
	}
	if _, err := os.Stat(testIdentityPath(root, testWorkspaceUID)); err != nil {
		t.Fatalf("workspace identity: %v", err)
	}
	if _, err := os.Stat(filepath.Join(target, ".hfl-workspace.json")); !os.IsNotExist(err) {
		t.Fatalf("identity must not be stored in restored data, err=%v", err)
	}
}

func TestRunLensKsPrepareRejectsPathOutsideRoot(t *testing.T) {
	root := newLensTestRoot(t)
	eng := New(nil)

	status, _, errMsg := eng.runLensKsPrepare(context.Background(), Payload{
		Path: "/tmp/outside",
		Extra: map[string]any{
			"workspace_root":         root,
			"workspace_uid":          testWorkspaceUID,
			"tenant_organization_id": 61,
			"gateway_link_id":        7,
			"knowledge_source_id":    42,
			"workspace_kind":         "managed_restore",
		},
	})
	if status != "failed" {
		t.Fatalf("expected failure, got status=%q", status)
	}
	if errMsg == "" {
		t.Fatal("expected error message")
	}
}

func TestRunLensKsPrepareRejectsManagedTrashPath(t *testing.T) {
	root := newLensTestRoot(t)
	status, _, errMsg := New(nil).runLensKsPrepare(context.Background(), Payload{
		Path: filepath.Join(root, lensWorkspaceTrashDirectory, "workspace-42"),
		Extra: map[string]any{
			"workspace_root":         root,
			"workspace_uid":          testWorkspaceUID,
			"tenant_organization_id": 61,
			"gateway_link_id":        7,
			"knowledge_source_id":    42,
			"workspace_kind":         "managed_restore",
		},
	})
	if status != "failed" || errMsg == "" {
		t.Fatalf("expected reserved trash rejection, status=%q err=%q", status, errMsg)
	}
}

func TestRunLensKsPrepareRejectsExistingDirectoryWithoutIdentity(t *testing.T) {
	root := newLensTestRoot(t)
	target := filepath.Join(root, "existing-user-data")
	if err := os.MkdirAll(target, 0o755); err != nil {
		t.Fatal(err)
	}
	marker := filepath.Join(target, "keep.txt")
	if err := os.WriteFile(marker, []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}

	status, _, errMsg := New(nil).runLensKsPrepare(context.Background(), Payload{
		Path: target,
		Extra: map[string]any{
			"workspace_root":         root,
			"workspace_uid":          testWorkspaceUID,
			"tenant_organization_id": 61,
			"gateway_link_id":        7,
			"knowledge_source_id":    42,
			"workspace_kind":         "managed_restore",
		},
	})

	if status != "failed" || errMsg == "" {
		t.Fatalf("expected unowned workspace rejection, status=%q err=%q", status, errMsg)
	}
	if _, err := os.Stat(marker); err != nil {
		t.Fatalf("existing workspace was modified: %v", err)
	}
	if _, err := os.Stat(testIdentityPath(root, testWorkspaceUID)); !os.IsNotExist(err) {
		t.Fatalf("unexpected identity claim, err=%v", err)
	}
}

func TestRunLensKsPrepareRecoversIdentityWithoutDirectory(t *testing.T) {
	root := newLensTestRoot(t)
	target := filepath.Join(root, "interrupted-workspace")
	payload := Payload{
		Path: target,
		Extra: map[string]any{
			"workspace_root":         root,
			"workspace_uid":          testWorkspaceUID,
			"tenant_organization_id": 61,
			"gateway_link_id":        7,
			"knowledge_source_id":    42,
			"workspace_kind":         "managed_restore",
		},
	}
	identity, err := lensWorkspaceIdentityFromPayload(payload)
	if err != nil {
		t.Fatal(err)
	}
	paths, err := resolveLensWorkspacePaths(target, root, identity.WorkspaceUID)
	if err != nil {
		t.Fatal(err)
	}
	if err := ensureLensMetadataLayout(paths); err != nil {
		t.Fatal(err)
	}
	if err := writeLensWorkspaceIdentity(paths.Identity, identity); err != nil {
		t.Fatal(err)
	}

	status, result, errMsg := New(nil).runLensKsPrepare(context.Background(), payload)

	if status != "success" {
		t.Fatalf("status=%q err=%q", status, errMsg)
	}
	if created, _ := result["created"].(bool); !created {
		t.Fatalf("expected interrupted directory creation to resume: %v", result)
	}
	if info, statErr := os.Stat(target); statErr != nil || !info.IsDir() {
		t.Fatalf("workspace was not recovered: info=%v err=%v", info, statErr)
	}
}

func TestRunLensKsCleanupRemovesOnlyMatchingManagedWorkspace(t *testing.T) {
	root := newLensTestRoot(t)
	target := filepath.Join(root, "tenant-61-ks-42")
	eng := New(nil)
	payload := Payload{
		Path: target,
		Extra: map[string]any{
			"workspace_root":         root,
			"workspace_uid":          testWorkspaceUID,
			"tenant_organization_id": 61,
			"gateway_link_id":        7,
			"knowledge_source_id":    42,
			"workspace_kind":         "managed_restore",
		},
	}
	if status, _, errMsg := eng.runLensKsPrepare(context.Background(), payload); status != "success" {
		t.Fatalf("prepare status=%q err=%q", status, errMsg)
	}
	status, result, errMsg := eng.runLensKsCleanup(context.Background(), payload)
	if status != "success" {
		t.Fatalf("cleanup status=%q err=%q result=%v", status, errMsg, result)
	}
	if _, err := os.Stat(target); !os.IsNotExist(err) {
		t.Fatalf("expected workspace removal, err=%v", err)
	}
}

func TestRunLensKsCleanupRejectsUnmanagedDirectory(t *testing.T) {
	root := newLensTestRoot(t)
	target := filepath.Join(root, "user-data")
	if err := os.MkdirAll(target, 0o755); err != nil {
		t.Fatal(err)
	}
	eng := New(nil)
	status, _, errMsg := eng.runLensKsCleanup(context.Background(), Payload{
		Path: target,
		Extra: map[string]any{
			"workspace_root":         root,
			"workspace_uid":          testWorkspaceUID,
			"tenant_organization_id": 61,
			"gateway_link_id":        7,
			"knowledge_source_id":    42,
			"workspace_kind":         "managed_restore",
		},
	})
	if status != "failed" || errMsg == "" {
		t.Fatalf("expected unmanaged cleanup rejection, status=%q err=%q", status, errMsg)
	}
	if _, err := os.Stat(target); err != nil {
		t.Fatalf("unmanaged directory was modified: %v", err)
	}
}

func TestRunLensKsCleanupRejectsUnverifiedTrashDirectory(t *testing.T) {
	root := newLensTestRoot(t)
	target := filepath.Join(root, "missing-workspace")
	trash := testTrashPath(root, testWorkspaceUID)
	if err := os.MkdirAll(trash, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(trash, "unrelated.txt"), []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}

	eng := New(nil)
	status, _, errMsg := eng.runLensKsCleanup(context.Background(), Payload{
		Path: target,
		Extra: map[string]any{
			"workspace_root":         root,
			"workspace_uid":          testWorkspaceUID,
			"tenant_organization_id": 61,
			"gateway_link_id":        7,
			"knowledge_source_id":    42,
			"workspace_kind":         "managed_restore",
		},
	})
	if status != "failed" || errMsg == "" {
		t.Fatalf("expected unverified trash rejection, status=%q err=%q", status, errMsg)
	}
	if _, err := os.Stat(filepath.Join(trash, "unrelated.txt")); err != nil {
		t.Fatalf("unverified trash was removed: %v", err)
	}
}

func TestRunLensKsPrepareRejectsIdentityOverwrite(t *testing.T) {
	root := newLensTestRoot(t)
	target := filepath.Join(root, "tenant-61-ks-42")
	eng := New(nil)
	payload := Payload{
		Path: target,
		Extra: map[string]any{
			"workspace_root":         root,
			"workspace_uid":          testWorkspaceUID,
			"tenant_organization_id": 61,
			"gateway_link_id":        7,
			"knowledge_source_id":    42,
			"workspace_kind":         "managed_restore",
		},
	}
	if status, _, errMsg := eng.runLensKsPrepare(context.Background(), payload); status != "success" {
		t.Fatalf("prepare status=%q err=%q", status, errMsg)
	}
	payload.Extra["tenant_organization_id"] = 62
	status, _, errMsg := eng.runLensKsPrepare(context.Background(), payload)
	if status != "failed" || errMsg == "" {
		t.Fatalf("expected identity mismatch, status=%q err=%q", status, errMsg)
	}
	identity, err := readLensWorkspaceIdentity(testIdentityPath(root, testWorkspaceUID))
	if err != nil {
		t.Fatal(err)
	}
	if identity.TenantOrganizationID != "61" {
		t.Fatalf("identity was overwritten: %+v", identity)
	}
}

func TestRunLensKsCleanupIdentityMismatchNeverDeletes(t *testing.T) {
	root := newLensTestRoot(t)
	target := filepath.Join(root, "tenant-61-ks-42")
	engine := New(nil)
	payload := Payload{
		Path: target,
		Extra: map[string]any{
			"workspace_root":         root,
			"workspace_uid":          testWorkspaceUID,
			"tenant_organization_id": 61,
			"gateway_link_id":        7,
			"knowledge_source_id":    42,
			"workspace_kind":         "managed_restore",
		},
	}
	if status, _, errMsg := engine.runLensKsPrepare(context.Background(), payload); status != "success" {
		t.Fatalf("prepare status=%q err=%q", status, errMsg)
	}
	payload.Extra["tenant_organization_id"] = 62
	status, _, errMsg := engine.runLensKsCleanup(context.Background(), payload)
	if status != "failed" || errMsg == "" {
		t.Fatalf("expected identity mismatch, status=%q err=%q", status, errMsg)
	}
	if _, err := os.Stat(target); err != nil {
		t.Fatalf("mismatched workspace was deleted: %v", err)
	}
}

func TestRunLensKsPrepareRejectsSymlinkEscape(t *testing.T) {
	root := newLensTestRoot(t)
	outside := t.TempDir()
	if err := os.Symlink(outside, filepath.Join(root, "escape")); err != nil {
		t.Fatal(err)
	}
	eng := New(nil)
	status, _, errMsg := eng.runLensKsPrepare(context.Background(), Payload{
		Path: filepath.Join(root, "escape", "ks-42"),
		Extra: map[string]any{
			"workspace_root":         root,
			"workspace_uid":          testWorkspaceUID,
			"tenant_organization_id": 61,
			"gateway_link_id":        7,
			"knowledge_source_id":    42,
			"workspace_kind":         "managed_restore",
		},
	})
	if status != "failed" || errMsg == "" {
		t.Fatalf("expected symlink rejection, status=%q err=%q", status, errMsg)
	}
	if _, err := os.Stat(filepath.Join(outside, "ks-42")); !os.IsNotExist(err) {
		t.Fatalf("outside directory was modified, err=%v", err)
	}
}

func TestValidateLensManagedRestoreTargetRejectsWorkspaceEscapeAndSymlink(t *testing.T) {
	root := newLensTestRoot(t)
	workspace := filepath.Join(root, "tenants", "61", "knowledge-sources", "workspace-42")
	payload := Payload{
		Path: workspace,
		Extra: map[string]any{
			"workspace_root":         root,
			"workspace_uid":          testWorkspaceUID,
			"tenant_organization_id": 61,
			"gateway_link_id":        7,
			"knowledge_source_id":    42,
			"workspace_kind":         "managed_restore",
			"managed_workspace_path": workspace,
		},
	}
	eng := New(nil)
	if status, _, errMsg := eng.runLensKsPrepare(context.Background(), payload); status != "success" {
		t.Fatalf("prepare status=%q err=%q", status, errMsg)
	}
	if err := validateLensManagedRestoreTarget(payload, filepath.Join(workspace, "docs", "file.txt")); err != nil {
		t.Fatalf("valid managed target rejected: %v", err)
	}
	if err := validateLensManagedRestoreTarget(payload, filepath.Join(root, "another-tenant")); err == nil {
		t.Fatal("expected workspace escape rejection")
	}
	outside := t.TempDir()
	if err := os.Symlink(outside, filepath.Join(workspace, "escape")); err != nil {
		t.Fatal(err)
	}
	if err := validateLensManagedRestoreTarget(payload, filepath.Join(workspace, "escape", "file.txt")); err == nil {
		t.Fatal("expected restore symlink rejection")
	}
}

func TestRunLensKsPrepareRejectsTraversalComponents(t *testing.T) {
	root := newLensTestRoot(t)
	payload := Payload{
		Path: filepath.Join(root, "tenant") + "/../../etc",
		Extra: map[string]any{
			"workspace_root":         root,
			"workspace_uid":          testWorkspaceUID,
			"tenant_organization_id": 61,
			"gateway_link_id":        7,
			"knowledge_source_id":    42,
			"workspace_kind":         "managed_restore",
		},
	}
	status, _, errMsg := New(nil).runLensKsPrepare(context.Background(), payload)
	if status != "failed" || errMsg == "" {
		t.Fatalf("expected traversal rejection, status=%q err=%q", status, errMsg)
	}
}

func TestRestoredLegacyMarkerCannotChangeExternalIdentity(t *testing.T) {
	root := newLensTestRoot(t)
	target := filepath.Join(root, "tenant-61-ks-42")
	payload := Payload{
		Path: target,
		Extra: map[string]any{
			"workspace_root":         root,
			"workspace_uid":          testWorkspaceUID,
			"tenant_organization_id": 61,
			"gateway_link_id":        7,
			"knowledge_source_id":    42,
			"workspace_kind":         "managed_restore",
			"managed_workspace_path": target,
		},
	}
	if status, _, errMsg := New(nil).runLensKsPrepare(context.Background(), payload); status != "success" {
		t.Fatalf("prepare status=%q err=%q", status, errMsg)
	}
	if err := os.WriteFile(filepath.Join(target, ".hfl-workspace.json"), []byte(`{"tenant_organization_id":"attacker"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := validateLensManagedRestoreTarget(payload, target); err != nil {
		t.Fatalf("restored legacy marker affected external identity: %v", err)
	}
}

func TestRunLensKsCleanupRetriesAfterPartialTrashRemoval(t *testing.T) {
	root := newLensTestRoot(t)
	target := filepath.Join(root, "tenant-61-ks-42")
	payload := Payload{
		Path: target,
		Extra: map[string]any{
			"workspace_root":         root,
			"workspace_uid":          testWorkspaceUID,
			"tenant_organization_id": 61,
			"gateway_link_id":        7,
			"knowledge_source_id":    42,
			"workspace_kind":         "managed_restore",
		},
	}
	engine := New(nil)
	if status, _, errMsg := engine.runLensKsPrepare(context.Background(), payload); status != "success" {
		t.Fatalf("prepare status=%q err=%q", status, errMsg)
	}
	if err := os.WriteFile(filepath.Join(target, "data.txt"), []byte("data"), 0o600); err != nil {
		t.Fatal(err)
	}
	originalRemove := removeLensWorkspaceTrash
	t.Cleanup(func() { removeLensWorkspaceTrash = originalRemove })
	removeLensWorkspaceTrash = func(path string) error {
		if err := os.Remove(filepath.Join(path, "data.txt")); err != nil {
			return err
		}
		return errors.New("injected partial removal")
	}
	status, _, _ := engine.runLensKsCleanup(context.Background(), payload)
	removeLensWorkspaceTrash = originalRemove
	if status != "failed" {
		t.Fatalf("expected injected failure, got %q", status)
	}
	tombstone, err := readLensWorkspaceTombstone(testTombstonePath(root, testWorkspaceUID))
	if err != nil || tombstone.State != lensWorkspaceTombstoneRetiring {
		t.Fatalf("expected durable retiring tombstone, tombstone=%+v err=%v", tombstone, err)
	}
	if prepareStatus, _, _ := New(nil).runLensKsPrepare(context.Background(), payload); prepareStatus != "failed" {
		t.Fatalf("late prepare must be rejected while cleanup is retiring, status=%q", prepareStatus)
	}
	if _, err := os.Stat(testIdentityPath(root, testWorkspaceUID)); err != nil {
		t.Fatalf("identity must survive partial removal: %v", err)
	}
	status, _, errMsg := engine.runLensKsCleanup(context.Background(), payload)
	if status != "success" {
		t.Fatalf("retry status=%q err=%q", status, errMsg)
	}
	if _, err := os.Stat(testIdentityPath(root, testWorkspaceUID)); !os.IsNotExist(err) {
		t.Fatalf("identity should be removed last, err=%v", err)
	}
	tombstone, err = readLensWorkspaceTombstone(testTombstonePath(root, testWorkspaceUID))
	if err != nil || tombstone.State != lensWorkspaceTombstoneRetired {
		t.Fatalf("expected durable retired tombstone, tombstone=%+v err=%v", tombstone, err)
	}
	if status, _, errMsg := New(nil).runLensKsCleanup(context.Background(), payload); status != "success" {
		t.Fatalf("repeated cleanup must be idempotent, status=%q err=%q", status, errMsg)
	}
	if status, _, _ := New(nil).runLensKsPrepare(context.Background(), payload); status != "failed" {
		t.Fatalf("retired UID must survive Agent restart and reject prepare, status=%q", status)
	}
}

func TestLensWorkspaceLifecycleLockSerializesPrepareAndCleanup(t *testing.T) {
	root := newLensTestRoot(t)
	target := filepath.Join(root, "tenant-61-ks-locked")
	payload := Payload{
		Path: target,
		Extra: map[string]any{
			"workspace_root":         root,
			"workspace_uid":          testWorkspaceUID,
			"tenant_organization_id": 61,
			"gateway_link_id":        7,
			"knowledge_source_id":    42,
			"workspace_kind":         "managed_restore",
		},
	}
	engine := New(nil)
	if status, _, errMsg := engine.runLensKsPrepare(context.Background(), payload); status != "success" {
		t.Fatalf("prepare status=%q err=%q", status, errMsg)
	}
	enteredRemoval := make(chan struct{})
	allowRemoval := make(chan struct{})
	originalRemove := removeLensWorkspaceTrash
	removeLensWorkspaceTrash = func(path string) error {
		close(enteredRemoval)
		<-allowRemoval
		return originalRemove(path)
	}
	t.Cleanup(func() { removeLensWorkspaceTrash = originalRemove })

	cleanupDone := make(chan string, 1)
	go func() {
		status, _, _ := engine.runLensKsCleanup(context.Background(), payload)
		cleanupDone <- status
	}()
	<-enteredRemoval
	prepareDone := make(chan string, 1)
	go func() {
		status, _, _ := engine.runLensKsPrepare(context.Background(), payload)
		prepareDone <- status
	}()
	if status := <-prepareDone; status != "failed" {
		t.Fatalf("late prepare must observe retiring tombstone, status=%q", status)
	}
	close(allowRemoval)
	if status := <-cleanupDone; status != "success" {
		t.Fatalf("cleanup status=%q", status)
	}
}

func TestLensWorkspaceLifecycleLockHonorsContext(t *testing.T) {
	lockPath := filepath.Join(t.TempDir(), "workspace.lock")
	lockHeld := make(chan struct{})
	releaseLock := make(chan struct{})
	firstDone := make(chan error, 1)
	go func() {
		firstDone <- withLensWorkspaceLock(
			context.Background(),
			lockPath,
			func() error {
				close(lockHeld)
				<-releaseLock
				return nil
			},
		)
	}()
	<-lockHeld
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	err := withLensWorkspaceLock(ctx, lockPath, func() error {
		t.Fatal("second action must not run while the UID lock is held")
		return nil
	})
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("expected lock deadline, got %v", err)
	}
	close(releaseLock)
	if err := <-firstDone; err != nil {
		t.Fatalf("first lock action failed: %v", err)
	}
}
