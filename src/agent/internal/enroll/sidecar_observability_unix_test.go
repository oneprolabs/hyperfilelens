//go:build !windows

package enroll

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestWriteLensEnvFileAtAppliesPlatformPolicyIdempotently(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lensnode.env")
	lens := LensSidecarConfig{
		LensBaseURL:   "https://lens.example.com",
		LensnodeUUID:  "26d1822b-3ccc-48f8-80f1-f4c0ae99e61e",
		LensnodeToken: "lens-token",
		LensnodeName:  "platform-lens",
		WorkspaceRoot: "/workspace",
		Observability: platformObservabilityPolicy(),
	}

	changed, fingerprint, err := writeLensEnvFileAt(path, lens)
	if err != nil {
		t.Fatal(err)
	}
	if !changed {
		t.Fatal("first write changed = false, want true")
	}
	if fingerprint == "" {
		t.Fatal("first write returned an empty fingerprint")
	}
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	text := string(content)
	for _, expected := range []string{
		"SENTRY_ENABLED=true",
		"SENTRY_BACKEND_DSN=https://public@sentry.example.com/25",
		"SENTRY_ENVIRONMENT=hfl-test",
		"HFL_SENTRY_LENSNODE_RELEASE=hyperfilelens-lensnode@main-123abcd-sl0.20.0",
	} {
		if !strings.Contains(text, expected+"\n") {
			t.Fatalf("lensnode.env missing %q:\n%s", expected, text)
		}
	}

	changed, secondFingerprint, err := writeLensEnvFileAt(path, lens)
	if err != nil {
		t.Fatal(err)
	}
	if changed {
		t.Fatal("second write changed = true, want false")
	}
	if secondFingerprint != fingerprint {
		t.Fatalf("fingerprint changed: %q != %q", secondFingerprint, fingerprint)
	}
}

func TestWriteLensEnvFileAtDisablesPrivateGateway(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lensnode.env")
	lens := LensSidecarConfig{
		LensBaseURL:   "https://lens.example.com",
		LensnodeUUID:  "26d1822b-3ccc-48f8-80f1-f4c0ae99e61e",
		LensnodeToken: "lens-token",
		WorkspaceRoot: "/workspace",
	}

	if _, _, err := writeLensEnvFileAt(path, lens); err != nil {
		t.Fatal(err)
	}
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	text := string(content)
	if !strings.Contains(text, "SENTRY_ENABLED=false\n") {
		t.Fatalf("private lensnode.env did not disable Sentry:\n%s", text)
	}
	if strings.Contains(text, "SENTRY_BACKEND_DSN=") {
		t.Fatalf("private lensnode.env contains a DSN field:\n%s", text)
	}
}

func TestLensObservabilityRetriesFailedApply(t *testing.T) {
	root := t.TempDir()
	attempts := 0
	runtime := lensSidecarRuntime{
		envPath:     filepath.Join(root, "lensnode.env"),
		appliedPath: filepath.Join(root, "state", "applied.sha256"),
		lockPath:    filepath.Join(root, "sidecar.lock"),
		healthy:     func() bool { return true },
		ensureImage: func(context.Context, Config) error { return nil },
		installSidecar: func(context.Context, Config, bool) error {
			attempts++
			if attempts == 1 {
				return errors.New("simulated compose failure")
			}
			return nil
		},
	}
	lens := LensSidecarConfig{
		LensBaseURL:   "https://lens.example.com",
		LensnodeUUID:  "26d1822b-3ccc-48f8-80f1-f4c0ae99e61e",
		LensnodeToken: "lens-token",
		WorkspaceRoot: "/workspace",
		Observability: platformObservabilityPolicy(),
	}

	if _, err := runtime.convergeObservability(context.Background(), Config{}, lens); err == nil {
		t.Fatal("first convergence unexpectedly succeeded")
	}
	if _, err := os.Stat(runtime.appliedPath); !os.IsNotExist(err) {
		t.Fatalf("failed apply recorded a fingerprint: %v", err)
	}
	changed, err := runtime.convergeObservability(context.Background(), Config{}, lens)
	if err != nil {
		t.Fatal(err)
	}
	if !changed || attempts != 2 {
		t.Fatalf("retry result changed=%v attempts=%d", changed, attempts)
	}
	changed, err = runtime.convergeObservability(context.Background(), Config{}, lens)
	if err != nil {
		t.Fatal(err)
	}
	if changed || attempts != 2 {
		t.Fatalf("idempotent result changed=%v attempts=%d", changed, attempts)
	}
}

func TestHealthyLensSidecarAppliesChangedConfiguration(t *testing.T) {
	root := t.TempDir()
	runs := 0
	runtime := lensSidecarRuntime{
		envPath:     filepath.Join(root, "lensnode.env"),
		appliedPath: filepath.Join(root, "state", "applied.sha256"),
		lockPath:    filepath.Join(root, "sidecar.lock"),
		healthy:     func() bool { return true },
		ensureImage: func(context.Context, Config) error { return nil },
		installSidecar: func(context.Context, Config, bool) error {
			runs++
			return nil
		},
	}
	privateLens := LensSidecarConfig{
		LensBaseURL:   "https://lens.example.com",
		LensnodeUUID:  "26d1822b-3ccc-48f8-80f1-f4c0ae99e61e",
		LensnodeToken: "lens-token",
		WorkspaceRoot: "/workspace",
	}
	_, privateFingerprint, err := writeLensEnvFileAt(runtime.envPath, privateLens)
	if err != nil {
		t.Fatal(err)
	}
	if err := markLensConfigurationApplied(runtime.appliedPath, privateFingerprint); err != nil {
		t.Fatal(err)
	}

	platformLens := privateLens
	platformLens.Observability = platformObservabilityPolicy()
	if err := runtime.install(context.Background(), Config{}, platformLens); err != nil {
		t.Fatal(err)
	}
	if runs != 1 {
		t.Fatalf("changed configuration installer runs = %d, want 1", runs)
	}
	if err := runtime.install(context.Background(), Config{}, platformLens); err != nil {
		t.Fatal(err)
	}
	if runs != 1 {
		t.Fatalf("unchanged configuration installer runs = %d, want 1", runs)
	}
}

func TestLegacyLensLayoutAuthorizationOnlyBeforeCanonicalEnv(t *testing.T) {
	root := t.TempDir()
	legacy := filepath.Join(root, "legacy", "lensnode.env")
	current := filepath.Join(root, "agent", "config", "lensnode.env")
	if err := os.MkdirAll(filepath.Dir(legacy), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(legacy, []byte("old-token\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Dir(current), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(current, []byte("new-token\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	if legacyLensLayoutPendingAt(current, legacy) {
		t.Fatal("existing canonical env must not be auto-authorized for legacy adoption")
	}
	if err := os.Remove(current); err != nil {
		t.Fatal(err)
	}
	if !legacyLensLayoutPendingAt(current, legacy) {
		t.Fatal("missing canonical env should authorize the first legacy adoption")
	}
	if err := os.Remove(legacy); err != nil {
		t.Fatal(err)
	}
	if legacyLensLayoutPendingAt(current, legacy) {
		t.Fatal("legacy layout should no longer be pending after successful cleanup")
	}
	legacyCompose := filepath.Join(filepath.Dir(legacy), "lensnode")
	if err := os.MkdirAll(legacyCompose, 0o700); err != nil {
		t.Fatal(err)
	}
	if !legacyLensLayoutPresentAt(legacy) {
		t.Fatal("leftover legacy Compose directory should remain visible to migration")
	}
	if !legacyLensLayoutPendingAt(current, legacy) {
		t.Fatal("leftover legacy Compose directory should trigger convergence when canonical env is absent")
	}
	if err := os.RemoveAll(legacyCompose); err != nil {
		t.Fatal(err)
	}
	if legacyLensLayoutPresentAt(legacy) {
		t.Fatal("legacy layout should be absent after Compose cleanup")
	}
}

func TestCustomAgentRootDoesNotObserveGlobalLegacyLensLayout(t *testing.T) {
	if got := gatewayLegacyLensEnvPath("/srv/custom-hfl-agent"); got != "" {
		t.Fatalf("custom Agent Root legacy env = %q, want empty", got)
	}
	if got := gatewayLegacyLensEnvPath("/opt/hyperfilelens-agent"); got != legacyLensEnvFilePath {
		t.Fatalf("standard Agent Root legacy env = %q, want %q", got, legacyLensEnvFilePath)
	}
}

func TestMarkLegacyLensLayoutAdoptedUsesPrivateRuntimeState(t *testing.T) {
	root := t.TempDir()
	appliedPath := filepath.Join(root, "runtime", "lensnode", ".hfl-applied-config.sha256")
	if err := markLegacyLensLayoutAdopted(appliedPath); err != nil {
		t.Fatal(err)
	}
	marker := filepath.Join(filepath.Dir(appliedPath), ".hfl-legacy-layout-adopted")
	info, err := os.Stat(marker)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("migration marker mode = %o, want 600", info.Mode().Perm())
	}
}

func TestAppliedConfigurationDoesNotSkipPendingLegacyCleanup(t *testing.T) {
	root := t.TempDir()
	legacy := filepath.Join(root, "legacy", "lensnode.env")
	if err := os.MkdirAll(filepath.Dir(legacy), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(legacy, []byte("legacy\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	runs := 0
	runtime := lensSidecarRuntime{
		envPath:       filepath.Join(root, "agent", "config", "lensnode.env"),
		appliedPath:   filepath.Join(root, "agent", "runtime", "lensnode", ".hfl-applied-config.sha256"),
		legacyEnvPath: legacy,
		lockPath:      filepath.Join(root, "sidecar.lock"),
		healthy:       func() bool { return true },
		ensureImage:   func(context.Context, Config) error { return nil },
		installSidecar: func(_ context.Context, _ Config, adopted bool) error {
			if adopted {
				t.Fatal("existing canonical env must not be marked as an authorized adoption")
			}
			runs++
			return os.Remove(legacy)
		},
	}
	lens := LensSidecarConfig{
		LensBaseURL:   "https://lens.example.com",
		LensnodeUUID:  "26d1822b-3ccc-48f8-80f1-f4c0ae99e61e",
		LensnodeToken: "lens-token",
		WorkspaceRoot: "/workspace",
	}
	_, fingerprint, err := writeLensEnvFileAt(runtime.envPath, lens)
	if err != nil {
		t.Fatal(err)
	}
	if err := markLensConfigurationApplied(runtime.appliedPath, fingerprint); err != nil {
		t.Fatal(err)
	}
	if err := runtime.install(context.Background(), Config{}, lens); err != nil {
		t.Fatal(err)
	}
	if runs != 1 {
		t.Fatalf("installer runs = %d, want 1", runs)
	}
	if _, err := os.Stat(filepath.Join(filepath.Dir(runtime.appliedPath), ".hfl-legacy-layout-adopted")); !os.IsNotExist(err) {
		t.Fatalf("conflicting pre-existing layout was unexpectedly marked adopted: %v", err)
	}
	if err := runtime.install(context.Background(), Config{}, lens); err != nil {
		t.Fatal(err)
	}
	if runs != 1 {
		t.Fatalf("cleanup reran after legacy state was removed: runs=%d", runs)
	}
}

func TestFileLockSerializesAndHonorsContext(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sidecar.lock")
	locked := make(chan struct{})
	release := make(chan struct{})
	firstDone := make(chan error, 1)
	go func() {
		firstDone <- withFileLock(context.Background(), path, func() error {
			close(locked)
			<-release
			return nil
		})
	}()
	<-locked

	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Millisecond)
	defer cancel()
	unexpectedRun := errors.New("second action ran while the first lock was held")
	err := withFileLock(ctx, path, func() error {
		return unexpectedRun
	})
	close(release)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("second lock error = %v, want deadline exceeded", err)
	}
	if err := <-firstDone; err != nil {
		t.Fatal(err)
	}
}
