package enroll

import (
	"strings"
	"testing"
)

func platformObservabilityPolicy() ObservabilityPolicy {
	return ObservabilityPolicy{
		Enabled:          true,
		BackendDSN:       "https://public@sentry.example.com/25",
		Environment:      "hfl-test",
		AgentRelease:     "hyperfilelens-agent@main-123abcd",
		LensnodeRelease:  "hyperfilelens-lensnode@main-123abcd-sl0.20.0",
		TracesSampleRate: 0,
	}
}

func TestObservabilityPolicyRejectsPrivateCredentialDSN(t *testing.T) {
	policy := platformObservabilityPolicy()
	policy.BackendDSN = "https://public:private@sentry.example.com/25"

	if got := policy.Normalized(); got.Enabled {
		t.Fatalf("Normalized() enabled = true for credential-bearing DSN: %#v", got)
	}
}

func TestParseGatewayLensConfigNormalizesObservabilityPolicy(t *testing.T) {
	raw := `{
        "node_id": 42,
        "lens": {
            "lens_base_url": "https://lens.example.com",
            "lensnode_uuid": "26d1822b-3ccc-48f8-80f1-f4c0ae99e61e",
            "lensnode_token": "lens-token",
            "workspace_root": "/workspace"
        },
        "observability": {
            "enabled": true,
            "backend_dsn": "https://public@sentry.example.com/25",
            "environment": "hfl-community",
            "agent_release": "hyperfilelens-agent@0.1.8",
            "lensnode_release": "hyperfilelens-lensnode@0.1.8-sl0.20.0",
            "traces_sample_rate": 0
        }
    }`

	lens, err := parseGatewayLensConfig([]byte(raw))
	if err != nil {
		t.Fatal(err)
	}
	if !lens.Observability.Enabled {
		t.Fatalf("observability policy was disabled: %#v", lens.Observability)
	}
	if lens.Observability.Environment != "hfl-community" {
		t.Fatalf("environment = %q", lens.Observability.Environment)
	}
}

func TestParseGatewayLensConfigFailsClosedForInvalidPolicy(t *testing.T) {
	raw := `{
        "lens": {
            "lens_base_url": "https://lens.example.com",
            "lensnode_uuid": "26d1822b-3ccc-48f8-80f1-f4c0ae99e61e",
            "lensnode_token": "lens-token"
        },
        "observability": {
            "enabled": true,
            "backend_dsn": "https://public:private@sentry.example.com/25",
            "environment": "hfl-production",
            "agent_release": "hyperfilelens-agent@0.1.8",
            "lensnode_release": "hyperfilelens-lensnode@0.1.8-sl0.20.0",
            "traces_sample_rate": 0
        }
    }`

	lens, err := parseGatewayLensConfig([]byte(raw))
	if err != nil {
		t.Fatal(err)
	}
	if lens.Observability.Enabled {
		t.Fatalf("credential-bearing policy was accepted: %#v", lens.Observability)
	}
	if strings.TrimSpace(lens.WorkspaceRoot) != "/workspace" {
		t.Fatalf("default workspace root = %q", lens.WorkspaceRoot)
	}
}
