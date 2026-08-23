package enroll

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	configstore "hyperfilelens/agent/internal/infra/config"
)

func TestResolveInstallationIDPrefersPersistedInstallation(t *testing.T) {
	t.Parallel()
	envPath := filepath.Join(t.TempDir(), "agent.env")
	if err := WriteInstallationID(envPath, "hfli_persisted"); err != nil {
		t.Fatal(err)
	}

	got, err := resolveInstallationID(envPath, Config{
		InstallationID: "hfli_process_override",
	})
	if err != nil {
		t.Fatal(err)
	}
	if got != "hfli_persisted" {
		t.Fatalf("installation id = %q, want persisted identity", got)
	}
}

func TestResolveInstallationIDReusesPersistedRetryIdentity(t *testing.T) {
	t.Parallel()
	envPath := filepath.Join(t.TempDir(), "agent.env")
	first, err := resolveInstallationID(envPath, Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := WriteInstallationID(envPath, first); err != nil {
		t.Fatal(err)
	}

	retry, err := resolveInstallationID(envPath, Config{})
	if err != nil {
		t.Fatal(err)
	}
	if retry != first {
		t.Fatalf("retry identity = %q, want %q", retry, first)
	}
}

func TestInstallationIDGeneratesWithoutPersistedState(t *testing.T) {
	t.Parallel()
	envPath := filepath.Join(t.TempDir(), "missing-agent.env")
	first, err := resolveInstallationID(envPath, Config{})
	if err != nil {
		t.Fatal(err)
	}
	second, err := resolveInstallationID(envPath, Config{})
	if err != nil {
		t.Fatal(err)
	}
	if first == second {
		t.Fatalf("independent installation identities unexpectedly match: %q", first)
	}
}

func TestRetiredInstallationIdentityCreatesNewInstallIdentity(t *testing.T) {
	t.Parallel()
	dataDir := t.TempDir()
	envPath := filepath.Join(dataDir, "config", "agent.env")
	if err := WriteInstallationID(envPath, "hfli_uninstalled"); err != nil {
		t.Fatal(err)
	}
	if err := configstore.RetireInstallationIdentity(dataDir); err != nil {
		t.Fatal(err)
	}

	next, err := resolveInstallationID(envPath, Config{})
	if err != nil {
		t.Fatal(err)
	}
	if next == "hfli_uninstalled" {
		t.Fatal("a completed uninstall reused the retired installation identity")
	}
}

func TestInstallFlowsPersistIdentityBeforeOpeningSession(t *testing.T) {
	t.Parallel()
	for _, filename := range []string{"install.go", "register_cmd.go"} {
		source, err := os.ReadFile(filename)
		if err != nil {
			t.Fatal(err)
		}
		text := string(source)
		persistAt := strings.Index(text, "persistInstallationID(cfg.InstallationID)")
		sessionAt := strings.Index(text, "enrollmentclient.OpenInstallationSession(")
		if persistAt < 0 || sessionAt < 0 || persistAt > sessionAt {
			t.Fatalf("%s must persist installation identity before opening a session", filename)
		}
	}
}
