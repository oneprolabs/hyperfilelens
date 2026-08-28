package install

import (
	"testing"

	"hyperfilelens/agent/internal/platform/vfs"
)

func TestUpgradeArtifactsStayUnderRollbackDirectory(t *testing.T) {
	root := "/opt/hyperfilelens-agent"
	if got, want := BackupStateLatestPath(root), root+"/backup/rollback/latest.tar.gz"; got != want {
		t.Fatalf("latest snapshot path = %q, want %q", got, want)
	}
	if got, want := BackupMetaPath(root), root+"/backup/rollback/meta.json"; got != want {
		t.Fatalf("snapshot metadata path = %q, want %q", got, want)
	}
	if got, want := BackupRollbackBinDir(root), root+"/backup/rollback/bin"; got != want {
		t.Fatalf("binary rollback path = %q, want %q", got, want)
	}
	if got, want := LifecycleUpgradeStatePath(root), root+"/lifecycle/upgrade-state.json"; got != want {
		t.Fatalf("upgrade state path = %q, want %q", got, want)
	}
	if got, want := vfs.AgentDatabasePath(root), root+"/data/agent.db"; got != want {
		t.Fatalf("database path = %q, want %q", got, want)
	}
}

func TestPathAllowedForRemoval(t *testing.T) {
	allowed := []string{
		"/opt/hyperfilelens-agent",
		"/opt/hyperfilelens-agent/backup",
		"/opt/hyperfilelens-agent/backup/rollback",
		"/opt/hyperfilelens-agent/backup/legacy",
		// Legacy state remains removable only for an explicit pre-unified
		// installation migration or purge retry.
		"/var/lib/hyperfilelens-agent",
		"/var/lib/hyperfilelens-agent/backup/rollback",
	}
	for _, path := range allowed {
		if !PathAllowedForRemoval(path) {
			t.Fatalf("expected allowed: %q", path)
		}
	}
	denied := []string{
		"",
		"/opt/other-agent",
		"/tmp/hyperfilelens-agent",
		"/var/lib/hyperfilelens-agent/../../../etc",
		"/opt/hyperfilelens-agent/../../etc",
		"var/lib/hyperfilelens-agent",
	}
	for _, path := range denied {
		if PathAllowedForRemoval(path) {
			t.Fatalf("expected denied: %q", path)
		}
	}
}
