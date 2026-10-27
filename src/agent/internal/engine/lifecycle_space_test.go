package engine

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/install"
	"hyperfilelens/agent/internal/platform/release"
)

func stubUpgradeSpaceCheck(t *testing.T, check func(string, string, uint64) error) {
	t.Helper()
	original := checkUpgradeSpace
	checkUpgradeSpace = check
	t.Cleanup(func() { checkUpgradeSpace = original })
}

func TestUpgradeFailsBeforeDownloadWhenLocalSpaceIsInsufficient(t *testing.T) {
	stubUpgradeDownloadDependencies(t)
	root := t.TempDir()
	failure := &install.UpgradeSpaceError{Code: install.UpgradeSpaceInsufficient}
	stubUpgradeSpaceCheck(t, func(data, bin string, minimum uint64) error {
		if data != root || bin == "" || minimum != install.DefaultUpgradeMinFreeBytes {
			t.Fatalf("check %s %s %d", data, bin, minimum)
		}
		return failure
	})
	downloadUpgradeArchive = func(context.Context, string, string, install.ProgressReporter) error {
		t.Fatal("download started before local check")
		return nil
	}
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: root}})
	status, result, _ := engine.runAgentUpgrade(context.Background(), ReporterSink{}, "space-before-download", Payload{})
	if status != "failed" || result["error_code"] != install.UpgradeSpaceInsufficient || result["old_agent_unchanged"] != true {
		t.Fatalf("status=%s result=%#v", status, result)
	}
	if _, err := os.Stat(install.RuntimeDownloadDir(root)); !os.IsNotExist(err) {
		t.Fatal("preflight must precede workspace mutations")
	}
}

func TestUpgradeRechecksSpaceAfterDownloadBeforeExtract(t *testing.T) {
	stubUpgradeDownloadDependencies(t)
	root := t.TempDir()
	calls := 0
	stubUpgradeSpaceCheck(t, func(string, string, uint64) error {
		calls++
		if calls == 2 {
			return &install.UpgradeSpaceError{Code: install.UpgradeSpaceUnavailable}
		}
		return nil
	})
	downloadUpgradeArchive = func(_ context.Context, _ string, path string, _ install.ProgressReporter) error {
		return os.WriteFile(path, []byte("not an archive"), 0600)
	}
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: root}})
	_, _, _, _, err := engine.prepareUpgradeBundle(context.Background(), ReporterSink{}, "space-after-download", Payload{Extra: map[string]any{"download_url": "https://example.invalid/package"}})
	var failure *upgradePreparationError
	if calls != 2 || !errors.As(err, &failure) || failure.result["error_code"] != install.UpgradeSpaceUnavailable {
		t.Fatalf("checks=%d err=%v", calls, err)
	}
	if _, err := os.Stat(install.RuntimeDownloadDir(root)); !os.IsNotExist(err) {
		t.Fatal("download artifacts were not cleaned")
	}
}

func TestUpgradeUsesExistingReleasePreparationEstimate(t *testing.T) {
	stubUpgradeDownloadDependencies(t)
	root := t.TempDir()
	want := uint64(2 * 1024 * 1024 * 1024)
	fetchUpgradeArtifact = func(context.Context, *model.AgentConfig) (release.Artifact, error) {
		return release.Artifact{DownloadURL: "https://example.invalid/package", Version: "1.2.0", RequiredSpace: want}, nil
	}
	original := checkUpgradeDownloadSpace
	t.Cleanup(func() { checkUpgradeDownloadSpace = original })
	checkUpgradeDownloadSpace = func(data string, minimum uint64) error {
		if data != root || minimum != want {
			t.Fatalf("preparation check: %s %d", data, minimum)
		}
		return &install.UpgradeSpaceError{Code: install.UpgradeSpaceInsufficient}
	}
	downloadUpgradeArchive = func(context.Context, string, string, install.ProgressReporter) error {
		t.Fatal("download must not start")
		return nil
	}
	version := "1.2.0"
	err := (&Engine{}).downloadUpgradeBundle(context.Background(), ReporterSink{}, "space-estimate", &model.AgentConfig{DataDir: root}, "", &version, filepath.Join(root, "package"))
	var failure *upgradePreparationError
	if !errors.As(err, &failure) || failure.result["error_code"] != install.UpgradeSpaceInsufficient {
		t.Fatal(err)
	}
}

func TestUpgradeRejectsInvalidMinimumAndKeepsSafetyFloor(t *testing.T) {
	t.Setenv("HFL_UPGRADE_MIN_FREE_MB", "")
	t.Setenv("HFL_UPGRADE_MIN_FREE_BYTES", "")
	for _, raw := range []any{-1, 1.5, "bad"} {
		if _, err := upgradeMinimumFromPayload(Payload{Extra: map[string]any{"min_free_bytes": raw}}); err == nil {
			t.Fatalf("accepted %v", raw)
		}
	}
	minimum, err := upgradeMinimumFromPayload(Payload{Extra: map[string]any{"min_free_bytes": 1}})
	if err != nil || minimum != install.DefaultUpgradeMinFreeBytes {
		t.Fatalf("minimum=%d err=%v", minimum, err)
	}
}
