package engine

import (
	"strings"
	"testing"
)

func TestS3SessionTokenParsedFingerprintedAndRedacted(t *testing.T) {
	spec, ok, err := parseRepositorySpec(map[string]any{
		"type": "s3", "bucket": "backup-bucket",
		"access_key_id": "test-access", "secret_access_key": "test-secret",
		"session_token": "test-session-token",
	})
	if err != nil || !ok || spec.SessionToken != "test-session-token" {
		t.Fatalf("temporary credentials not parsed: %v", err)
	}
	first, err := s3ConnectionFingerprint(spec)
	if err != nil {
		t.Fatal(err)
	}
	spec.SessionToken = "replacement-session-token"
	second, err := s3ConnectionFingerprint(spec)
	if err != nil || first == second {
		t.Fatal("session token rotation did not invalidate the connection")
	}
	text := redactManagedRestoreSecrets("failed: replacement-session-token", spec, Payload{})
	if strings.Contains(text, spec.SessionToken) {
		t.Fatal("temporary token was not redacted")
	}
}

func TestManagedS3RepositoryCannotFallBackToLegacyCredentials(t *testing.T) {
	for _, mode := range []string{"repository_temporary_identity_v1", "unsupported-mode"} {
		if _, _, err := parseRepositorySpec(map[string]any{
			"type": "s3", "bucket": "backup-bucket", "credential_mode": mode,
			"access_key_id": "legacy-access", "secret_access_key": "legacy-secret",
		}); err == nil {
			t.Fatal("managed repository accepted without an execution-bound credential lease")
		}
	}
	spec := repositorySpec{CredentialLease: &repositoryCredentialLease{Token: "signed-execution-grant"}}
	if strings.Contains(redactManagedRestoreSecrets("failed: signed-execution-grant", spec, Payload{}), "signed-execution-grant") {
		t.Fatal("execution capability was not redacted")
	}
}
