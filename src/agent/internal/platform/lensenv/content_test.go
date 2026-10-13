package lensenv

import (
	"crypto/sha256"
	"fmt"
	"testing"
)

func TestContentMatchesExistingSidecarFormat(t *testing.T) {
	values := map[string]string{
		"LENS_BASE_URL":  "https://private.example/sourcelens",
		"LENSNODE_TOKEN": "existing-token", "LENSNODE_UUID": "existing-uuid",
		"HFL_WORKSPACE_ROOT": "/backup workspace", "LENSNODE_NAME": "Gateway",
		"SENTRY_ENABLED": "false", "UNKNOWN_LOCAL_FIELD": "preserved elsewhere",
	}
	want := "# HyperFileLens SourceLens LensNode sidecar (managed by hfl-enroll gateway-install)\n" +
		"LENS_BASE_URL=https://private.example/sourcelens\n" +
		"LENSNODE_TOKEN=existing-token\nLENSNODE_UUID=existing-uuid\n" +
		"HFL_WORKSPACE_ROOT=\"/backup workspace\"\nLENSNODE_NAME=Gateway\nSENTRY_ENABLED=false\n"
	if got := string(Content(values)); got != want {
		t.Fatalf("canonical content changed:\n%s", got)
	}
	if Fingerprint(values) != fmt.Sprintf("%x", sha256.Sum256([]byte(want))) {
		t.Fatal("applied fingerprint does not match existing SHA-256 format")
	}
}
