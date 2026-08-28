package cli

import "testing"

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
