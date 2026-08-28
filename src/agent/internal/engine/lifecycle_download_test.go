package engine

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/install"
	"hyperfilelens/agent/internal/platform/release"
)

type recordingProgressSink struct {
	events []map[string]any
}

func (sink *recordingProgressSink) OnProgress(_ context.Context, progress map[string]any) error {
	sink.events = append(sink.events, progress)
	return nil
}

func stubUpgradeDownloadDependencies(t *testing.T) {
	t.Helper()
	originalFetch := fetchUpgradeArtifact
	originalDownload := downloadUpgradeArchive
	originalWait := waitUpgradeRetry
	originalDelays := upgradeRetryDelays
	t.Cleanup(func() {
		fetchUpgradeArtifact = originalFetch
		downloadUpgradeArchive = originalDownload
		waitUpgradeRetry = originalWait
		upgradeRetryDelays = originalDelays
	})
	waitUpgradeRetry = func(context.Context, time.Duration) error { return nil }
	upgradeRetryDelays = []time.Duration{0, 0}
}

func TestOnlineUpgradeDownloadRetriesWithFreshURL(t *testing.T) {
	stubUpgradeDownloadDependencies(t)
	urls := []string{"https://example.invalid/one", "https://example.invalid/two"}
	fetchCalls := 0
	fetchUpgradeArtifact = func(context.Context, *model.AgentConfig) (release.Artifact, error) {
		url := urls[fetchCalls]
		fetchCalls++
		return release.Artifact{DownloadURL: url, Version: "1.2.3"}, nil
	}
	var downloadedURLs []string
	downloadUpgradeArchive = func(_ context.Context, rawURL, destination string, reporter install.ProgressReporter) error {
		downloadedURLs = append(downloadedURLs, rawURL)
		if len(downloadedURLs) == 1 {
			return install.ErrDownloadNoProgress
		}
		if reporter != nil {
			reporter(install.DownloadProgress{DownloadedBytes: 7, TotalBytes: 7, Completed: true})
		}
		return os.WriteFile(destination, []byte("package"), 0o600)
	}

	target := "1.2.3"
	destination := filepath.Join(t.TempDir(), "package.tar.gz")
	sink := &recordingProgressSink{}
	err := (&Engine{}).downloadUpgradeBundle(
		context.Background(),
		ReporterSink{Sink: sink, TaskID: "task-1"},
		"task-1",
		&model.AgentConfig{},
		"",
		&target,
		destination,
	)
	if err != nil {
		t.Fatalf("downloadUpgradeBundle failed: %v", err)
	}
	if !reflect.DeepEqual(downloadedURLs, urls) {
		t.Fatalf("download URLs = %#v, want %#v", downloadedURLs, urls)
	}
	if fetchCalls != 2 {
		t.Fatalf("release fetch calls = %d, want 2", fetchCalls)
	}
	if _, err := os.Stat(destination); err != nil {
		t.Fatalf("download destination: %v", err)
	}
	if !hasDownloadState(sink.events, "retry_wait") || !hasDownloadState(sink.events, "completed") {
		t.Fatalf("progress events do not include retry and completion: %#v", sink.events)
	}
}

func TestOnlineUpgradeDownloadDoesNotRetryDeterministicFailure(t *testing.T) {
	stubUpgradeDownloadDependencies(t)
	fetchUpgradeArtifact = func(context.Context, *model.AgentConfig) (release.Artifact, error) {
		return release.Artifact{DownloadURL: "https://example.invalid/package", Version: "1.2.3"}, nil
	}
	downloadCalls := 0
	downloadUpgradeArchive = func(context.Context, string, string, install.ProgressReporter) error {
		downloadCalls++
		return &install.DownloadHTTPError{StatusCode: 404, Status: "404 Not Found"}
	}

	target := "1.2.3"
	err := (&Engine{}).downloadUpgradeBundle(
		context.Background(),
		ReporterSink{},
		"task-2",
		&model.AgentConfig{},
		"",
		&target,
		filepath.Join(t.TempDir(), "package.tar.gz"),
	)
	if err == nil || downloadCalls != 1 {
		t.Fatalf("error = %v, download calls = %d; want one failed attempt", err, downloadCalls)
	}
	var failure *upgradePreparationError
	if !errors.As(err, &failure) {
		t.Fatalf("error type = %T, want upgradePreparationError", err)
	}
	if failure.result["error_code"] != "AGENT_PACKAGE_DOWNLOAD_HTTP_ERROR" {
		t.Fatalf("failure result = %#v", failure.result)
	}
	if failure.result["old_agent_unchanged"] != true {
		t.Fatalf("old Agent evidence missing: %#v", failure.result)
	}
}

func TestOnlineUpgradeDownloadRejectsChangedTarget(t *testing.T) {
	stubUpgradeDownloadDependencies(t)
	fetchUpgradeArtifact = func(context.Context, *model.AgentConfig) (release.Artifact, error) {
		return release.Artifact{DownloadURL: "https://example.invalid/package", Version: "1.2.4"}, nil
	}
	downloadUpgradeArchive = func(context.Context, string, string, install.ProgressReporter) error {
		t.Fatal("download must not start after the release target changes")
		return nil
	}

	target := "1.2.3"
	err := (&Engine{}).downloadUpgradeBundle(
		context.Background(),
		ReporterSink{},
		"task-3",
		&model.AgentConfig{},
		"",
		&target,
		filepath.Join(t.TempDir(), "package.tar.gz"),
	)
	var failure *upgradePreparationError
	if !errors.As(err, &failure) || failure.result["error_code"] != "AGENT_PACKAGE_TARGET_CHANGED" {
		t.Fatalf("changed-target error = %v, failure = %#v", err, failure)
	}
}

func TestOnlineUpgradeDownloadRefreshesForbiddenURLOnlyOnce(t *testing.T) {
	stubUpgradeDownloadDependencies(t)
	fetchCalls := 0
	fetchUpgradeArtifact = func(context.Context, *model.AgentConfig) (release.Artifact, error) {
		fetchCalls++
		return release.Artifact{
			DownloadURL: "https://example.invalid/package",
			Version:     "1.2.3",
		}, nil
	}
	downloadCalls := 0
	downloadUpgradeArchive = func(context.Context, string, string, install.ProgressReporter) error {
		downloadCalls++
		return &install.DownloadHTTPError{StatusCode: 403, Status: "403 Forbidden"}
	}

	target := "1.2.3"
	err := (&Engine{}).downloadUpgradeBundle(
		context.Background(),
		ReporterSink{},
		"task-4",
		&model.AgentConfig{},
		"https://example.invalid/expired",
		&target,
		filepath.Join(t.TempDir(), "package.tar.gz"),
	)
	if err == nil || downloadCalls != 2 || fetchCalls != 1 {
		t.Fatalf("error = %v, downloads = %d, release refreshes = %d", err, downloadCalls, fetchCalls)
	}
}

func hasDownloadState(events []map[string]any, state string) bool {
	for _, event := range events {
		download, ok := event["download"].(map[string]any)
		if ok && download["state"] == state {
			return true
		}
	}
	return false
}
