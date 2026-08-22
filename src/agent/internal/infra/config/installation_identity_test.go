package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRetireInstallationIdentityPreservesNonIdentityConfiguration(t *testing.T) {
	t.Parallel()
	dataDir := t.TempDir()
	envPath := filepath.Join(dataDir, "config", agentEnvFileName)
	jsonPath := filepath.Join(dataDir, "config", configJSONName)
	envContent := strings.Join([]string{
		"# preserved comment",
		"HFL_API_BASE=https://console.example",
		"HFL_INSTALLATION_ID=hfli_old",
		"HFL_NODE_ID=42",
		"HFL_NODE_CREDENTIAL=hfln_old",
		"HFL_NODE_TOKEN=legacy-old",
		"SENTRY_ENABLED=true",
		"",
	}, "\n")
	jsonContent := `{
  "api_base_url": "https://console.example",
  "installation_id": "hfli_old",
  "node_id": "42",
  "node_token": "hfln_old",
  "future_setting": {"enabled": true}
}
`
	if err := os.MkdirAll(filepath.Dir(envPath), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(envPath, []byte(envContent), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(jsonPath, []byte(jsonContent), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := RetireInstallationIdentity(dataDir); err != nil {
		t.Fatal(err)
	}

	envResult, err := os.ReadFile(envPath)
	if err != nil {
		t.Fatal(err)
	}
	jsonResult, err := os.ReadFile(jsonPath)
	if err != nil {
		t.Fatal(err)
	}
	for _, forbidden := range []string{
		"HFL_INSTALLATION_ID",
		"HFL_NODE_ID",
		"HFL_NODE_CREDENTIAL",
		"HFL_NODE_TOKEN",
		"\"installation_id\"",
		"\"node_id\"",
		"\"node_token\"",
	} {
		if strings.Contains(string(envResult)+string(jsonResult), forbidden) {
			t.Errorf("retired identity still contains %q", forbidden)
		}
	}
	for _, preserved := range []string{
		"HFL_API_BASE=https://console.example",
		"SENTRY_ENABLED=true",
		"\"api_base_url\": \"https://console.example\"",
		"\"future_setting\"",
	} {
		if !strings.Contains(string(envResult)+string(jsonResult), preserved) {
			t.Errorf("retirement removed %q", preserved)
		}
	}
}

func TestRetireInstallationIdentityIsIdempotentWithoutConfigFiles(t *testing.T) {
	t.Parallel()
	if err := RetireInstallationIdentity(t.TempDir()); err != nil {
		t.Fatal(err)
	}
}

func TestClearNodeTokenJSONOverridePreservesOtherSettings(t *testing.T) {
	t.Parallel()
	dataDir := t.TempDir()
	jsonPath := filepath.Join(dataDir, "config", configJSONName)
	content := `{
  "api_base_url": "https://console.example",
  "node_token": "stale-enrollment-token",
  "future_setting": {"enabled": true}
}
`
	if err := os.MkdirAll(filepath.Dir(jsonPath), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(jsonPath, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := ClearNodeTokenJSONOverride(dataDir); err != nil {
		t.Fatal(err)
	}

	result, err := os.ReadFile(jsonPath)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(result), "node_token") {
		t.Fatalf("stale JSON credential remains: %s", result)
	}
	for _, preserved := range []string{
		`"api_base_url": "https://console.example"`,
		`"future_setting"`,
	} {
		if !strings.Contains(string(result), preserved) {
			t.Errorf("credential cleanup removed %q", preserved)
		}
	}
}
