package cli

import (
	"errors"
	"testing"
	"time"

	"hyperfilelens/agent/internal/platform/install"
)

func TestVersionIsARealSubcommand(t *testing.T) {
	if !IsSubcommand("version") {
		t.Fatal("version must be dispatched as a short-lived CLI command")
	}
}

func TestPackageIsARealSubcommand(t *testing.T) {
	if !IsSubcommand("package") {
		t.Fatal("package must be dispatched as a short-lived CLI command")
	}
}

func TestDatabaseIsARealSubcommand(t *testing.T) {
	if !IsSubcommand("database") {
		t.Fatal("database must be dispatched as a short-lived CLI command")
	}
}

func TestDatabaseBackupRequiresBothPaths(t *testing.T) {
	err := runDatabase([]string{"backup", "--source", "agent.db"})
	if err == nil || err.Error() != "database backup requires --source and --destination" {
		t.Fatalf("runDatabase() error = %v", err)
	}
}

func TestPackageVerifyRequiresRoot(t *testing.T) {
	err := runPackage([]string{"verify", "--role", "agent"})
	if err == nil || err.Error() != "package verify requires --root <directory>" {
		t.Fatalf("runPackage() error = %v", err)
	}
}

func TestPackageVerifyRejectsUnknownRole(t *testing.T) {
	err := runPackage([]string{"verify", "--root", t.TempDir(), "--role", "unknown"})
	if err == nil || err.Error() != `unsupported package role "unknown"` {
		t.Fatalf("runPackage() error = %v", err)
	}
}

func TestPackageCheckSpaceRequiresAbsolutePaths(t *testing.T) {
	if err := runPackage([]string{"check-space"}); err == nil {
		t.Fatal("missing paths accepted")
	}
	if err := runPackage([]string{"check-space", "--data-dir", "relative", "--install-dir", "relative"}); err == nil {
		t.Fatal("relative paths accepted")
	}
}

func TestPackageCheckSpaceRecordsDetachedDiagnostics(t *testing.T) {
	root := t.TempDir()
	started := time.Now().UTC()
	t.Setenv("HFL_UPGRADE_MIN_FREE_MB", "")
	t.Setenv("HFL_UPGRADE_MIN_FREE_BYTES", "18446744073709551615")
	err := runPackage([]string{
		"check-space", "--record-failure", "--data-dir", root, "--install-dir", root,
	})
	var failure *install.UpgradeSpaceError
	if !errors.As(err, &failure) || failure.Code != install.UpgradeSpaceInsufficient {
		t.Fatalf("failure = %v", err)
	}
	record := install.ReadUpgradeSpaceFailure(root, &started)
	if record == nil || record.Code != failure.Code {
		t.Fatalf("missing detached diagnostics: %#v", record)
	}
}
