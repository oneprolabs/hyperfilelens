// Package lensenv shares the existing sidecar's canonical configuration format.
package lensenv

import (
	"crypto/sha256"
	"fmt"
	"strings"
)

// Content renders the same managed fields, order and quoting as enrollment.
// Unknown local fields are deliberately excluded from the applied fingerprint.
func Content(values map[string]string) []byte {
	lines := []string{"# HyperFileLens SourceLens LensNode sidecar (managed by hfl-enroll gateway-install)"}
	for _, key := range []string{"LENS_BASE_URL", "LENSNODE_TOKEN", "LENSNODE_UUID", "HFL_WORKSPACE_ROOT"} {
		lines = append(lines, key+"="+Quote(values[key]))
	}
	if values["LENSNODE_NAME"] != "" {
		lines = append(lines, "LENSNODE_NAME="+Quote(values["LENSNODE_NAME"]))
	}
	for _, key := range []string{"SENTRY_ENABLED", "SENTRY_BACKEND_DSN", "SENTRY_ENVIRONMENT", "SENTRY_TRACES_SAMPLE_RATE", "HFL_SENTRY_LENSNODE_RELEASE"} {
		if value, present := values[key]; present {
			lines = append(lines, key+"="+Quote(value))
		}
	}
	return []byte(strings.Join(lines, "\n") + "\n")
}

// Fingerprint is the existing SHA-256 of the canonical sidecar content.
func Fingerprint(values map[string]string) string {
	return fmt.Sprintf("%x", sha256.Sum256(Content(values)))
}

// Quote retains the sidecar installer's existing env quoting convention.
func Quote(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return `""`
	}
	if !strings.ContainsAny(value, " \t$\"'\\") {
		return value
	}
	return `"` + strings.ReplaceAll(value, `"`, `\"`) + `"`
}
