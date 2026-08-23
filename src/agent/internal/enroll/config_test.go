package enroll

import (
	"os/user"
	"testing"

	"hyperfilelens/agent/internal/model"
)

func setRequiredEnrollmentEnv(t *testing.T) {
	t.Helper()
	t.Setenv("HFL_ORG_KEY", "test-org")
	t.Setenv("HFL_NODE_TOKEN", "test-token")
	t.Setenv("HFL_API_BASE", "https://console.example")
}

func TestLoadConfigDefaultsToSystemInstallation(t *testing.T) {
	setRequiredEnrollmentEnv(t)
	t.Setenv("HFL_NODE_ROLE", "agent")
	t.Setenv("HFL_INSTALLATION_MODE", "")

	cfg, err := LoadConfigFromEnv()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.InstallationMode != model.InstallationModeSystem {
		t.Fatalf("installation mode = %q", cfg.InstallationMode)
	}
}

func TestLoadConfigAcceptsUserLevelSourceAgent(t *testing.T) {
	setRequiredEnrollmentEnv(t)
	t.Setenv("HFL_NODE_ROLE", "agent")
	t.Setenv("HFL_INSTALLATION_MODE", "user")

	cfg, err := LoadConfigFromEnv()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.InstallationMode != model.InstallationModeUser {
		t.Fatalf("installation mode = %q", cfg.InstallationMode)
	}
}

func TestLoadConfigAcceptsSpecifiedUserContinuousSourceAgent(t *testing.T) {
	setRequiredEnrollmentEnv(t)
	t.Setenv("HFL_NODE_ROLE", "agent")
	t.Setenv("HFL_INSTALLATION_MODE", "account")
	t.Setenv("HFL_RUN_AS_USER", "backup-user")

	cfg, err := LoadConfigFromEnv()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.InstallationMode != model.InstallationModeAccount || cfg.RunAsUser != "backup-user" {
		t.Fatalf("specified-user config = mode %q, account %q", cfg.InstallationMode, cfg.RunAsUser)
	}
}

func TestLoadConfigInfersSpecifiedUserFromSudoInvoker(t *testing.T) {
	invokingAccount, err := user.Current()
	if err != nil || invokingAccount.Username == "root" {
		t.Skip("requires a non-root local account")
	}
	setRequiredEnrollmentEnv(t)
	t.Setenv("HFL_NODE_ROLE", "agent")
	t.Setenv("HFL_INSTALLATION_MODE", "account")
	t.Setenv("HFL_RUN_AS_USER", "")
	t.Setenv("HFL_RUN_AS_HOME", "")
	t.Setenv("SUDO_USER", invokingAccount.Username)

	cfg, err := LoadConfigFromEnv()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.RunAsUser != invokingAccount.Username {
		t.Fatalf("RunAsUser = %q, want sudo invoker", cfg.RunAsUser)
	}
	if cfg.RunAsHome != invokingAccount.HomeDir {
		t.Fatalf("RunAsHome = %q, want %q", cfg.RunAsHome, invokingAccount.HomeDir)
	}
}

func TestLoadConfigDoesNotInferRootAsSpecifiedUser(t *testing.T) {
	setRequiredEnrollmentEnv(t)
	t.Setenv("HFL_NODE_ROLE", "agent")
	t.Setenv("HFL_INSTALLATION_MODE", "account")
	t.Setenv("HFL_RUN_AS_USER", "")
	t.Setenv("SUDO_USER", "root")

	cfg, err := LoadConfigFromEnv()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.RunAsUser != "" {
		t.Fatalf("RunAsUser = %q, root must not be selected", cfg.RunAsUser)
	}
}

func TestLoadConfigRejectsSpecifiedUserInfrastructureRole(t *testing.T) {
	setRequiredEnrollmentEnv(t)
	t.Setenv("HFL_NODE_ROLE", "proxy")
	t.Setenv("HFL_INSTALLATION_MODE", "account")

	if _, err := LoadConfigFromEnv(); err == nil {
		t.Fatal("expected specified-user proxy configuration to be rejected")
	}
}

func TestLoadConfigRejectsUserLevelInfrastructureRole(t *testing.T) {
	setRequiredEnrollmentEnv(t)
	t.Setenv("HFL_NODE_ROLE", "gateway")
	t.Setenv("HFL_INSTALLATION_MODE", "user")

	if _, err := LoadConfigFromEnv(); err == nil {
		t.Fatal("expected user-level gateway configuration to be rejected")
	}
}
