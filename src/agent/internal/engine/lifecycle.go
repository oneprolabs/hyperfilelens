package engine

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math/rand/v2"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"time"

	"hyperfilelens/agent/internal/enroll"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/install"
	"hyperfilelens/agent/internal/platform/release"
	"hyperfilelens/agent/internal/platform/tlsclient"
	"hyperfilelens/agent/internal/platform/vfs"
	"hyperfilelens/agent/internal/selfupdate"
)

const (
	upgradeDownloadMaxAttempts   = 3
	upgradeDownloadProgressEvery = 3 * time.Second
)

var (
	fetchUpgradeArtifact   = release.FetchArtifact
	downloadUpgradeArchive = install.DownloadURLWithProgress
	upgradeRetryDelays     = []time.Duration{5 * time.Second, 15 * time.Second}
	waitUpgradeRetry       = waitWithContext
)

type upgradePreparationError struct {
	err    error
	result map[string]any
}

func (failure *upgradePreparationError) Error() string { return failure.err.Error() }
func (failure *upgradePreparationError) Unwrap() error { return failure.err }

func (e *Engine) runAgentUpgrade(ctx context.Context, rep ReporterSink, taskID string, p Payload) (string, map[string]any, string) {
	if e.current().InstallationMode == model.InstallationModeAccount {
		return "failed", nil, "specified-user continuous protection requires administrator authorization for upgrade; run the generated local administrator command"
	}
	archivePath, targetVersion, workDir, bundleRoot, err := e.prepareUpgradeBundle(ctx, rep, taskID, p)
	if err != nil {
		var failure *upgradePreparationError
		if errors.As(err, &failure) {
			return "failed", failure.result, err.Error()
		}
		return "failed", nil, err.Error()
	}
	defer os.RemoveAll(workDir)

	cfg := e.current()
	dataDir := strings.TrimSpace(cfg.DataDir)
	if dataDir == "" {
		dataDir = vfs.DefaultAgentDataDir()
	}
	logDir := strings.TrimSpace(cfg.LogDir)
	if logDir == "" {
		logDir = vfs.AgentLogDir(dataDir)
	}
	installDir := vfs.LifecycleInstallDirForMode(cfg.InstallationMode)
	upgradeLog := install.UpgradeLogPath(logDir)

	stagedArchive, err := install.StageUpgradeArchive(dataDir, archivePath)
	if err != nil {
		return "failed", nil, err.Error()
	}
	stagedInstaller, err := install.StageUpgradeInstaller(dataDir, bundleRoot)
	if err != nil {
		return "failed", nil, err.Error()
	}

	_ = sendProgress(ctx, rep, taskID, map[string]any{
		"phase":       "upgrade",
		"mode":        "local_detached",
		"upgrade_log": upgradeLog,
	})
	if err := install.ScheduleDetachedUpgrade(
		stagedArchive,
		stagedInstaller,
		logDir,
		cfg.InstallationMode == model.InstallationModeUser || cfg.InstallationMode == model.InstallationModeUserContinuous,
	); err != nil {
		slog.Warn("detached upgrade schedule failed", "err", err, "upgrade_log", upgradeLog)
		if !install.ShouldRetainDetachedLifecycleFiles(err) {
			_ = os.RemoveAll(filepath.Dir(stagedArchive))
		}
		return "failed", nil, err.Error()
	}
	slog.Info("detached upgrade scheduled", "install_dir", installDir, "archive", stagedArchive, "upgrade_log", upgradeLog)
	// Keep task running: service stop + WS drop are expected while detached install.sh runs.
	return "running", map[string]any{
		"previous_version": selfupdate.Version,
		"target_version":   targetVersion,
		"mode":             "local_detached",
		"upgrade_log":      upgradeLog,
	}, ""
}

func (e *Engine) runAgentUninstall(ctx context.Context, rep ReporterSink, taskID string, p Payload) (string, map[string]any, string) {
	keepData := false
	if v, ok := p.Extra["keep_data"].(bool); ok {
		keepData = v
	}
	forceCleanup := false
	if v, ok := p.Extra["force_cleanup"].(bool); ok {
		forceCleanup = v
	}

	cfg := e.current()
	if cfg.InstallationMode == model.InstallationModeAccount {
		return "failed", nil, "specified-user continuous protection requires administrator authorization for uninstall; run the generated local administrator command"
	}
	dataDir := strings.TrimSpace(cfg.DataDir)
	if dataDir == "" {
		dataDir = vfs.DefaultAgentDataDir()
	}
	installDir := vfs.LifecycleInstallDirForMode(cfg.InstallationMode)
	logDir := strings.TrimSpace(cfg.LogDir)
	if logDir == "" {
		logDir = vfs.AgentLogDir(dataDir)
	}
	uninstallLog := install.UninstallLogPath(logDir)
	completionPayload, ok := p.Extra["completion"].(map[string]any)
	if !ok {
		return "failed", nil, "signed uninstall completion configuration is required"
	}
	completion := install.UninstallCompletion{
		APIBaseURL:   cfg.APIBaseURL,
		Path:         payloadStringValue(completionPayload["path"]),
		Token:        payloadStringValue(completionPayload["token"]),
		InsecureTLS:  tlsclient.InsecureTLSEnabled(),
		ForceCleanup: forceCleanup,
	}
	if _, err := completion.CallbackURL(); err != nil {
		return "failed", nil, err.Error()
	}

	_ = sendProgress(ctx, rep, taskID, map[string]any{
		"phase":         "uninstall",
		"keep_data":     keepData,
		"mode":          "local_detached",
		"uninstall_log": uninstallLog,
	})

	if err := install.ScheduleDetachedUninstall(
		installDir,
		dataDir,
		logDir,
		keepData,
		cfg.InstallationMode == model.InstallationModeUser || cfg.InstallationMode == model.InstallationModeUserContinuous,
		completion,
	); err != nil {
		slog.Warn("detached uninstall schedule failed", "err", err, "uninstall_log", uninstallLog)
		return "failed", nil, err.Error()
	}
	slog.Info("detached uninstall scheduled", "install_dir", installDir, "data_dir", dataDir, "uninstall_log", uninstallLog)
	return "running", map[string]any{
		"keep_data":     keepData,
		"mode":          "local_detached",
		"uninstall_log": uninstallLog,
	}, ""
}

func runBundleUninstall(ctx context.Context, bundleRoot string, keepData bool) error {
	if runtime.GOOS == "windows" {
		args := []string{"-NoProfile", "-File", filepath.Join(bundleRoot, "install.ps1"), "uninstall"}
		if !keepData {
			args = append(args, "-PurgeAll")
		}
		out, err := exec.CommandContext(ctx, "powershell", args...).CombinedOutput()
		if err != nil {
			return fmt.Errorf("install.ps1 uninstall: %w (%s)", err, strings.TrimSpace(string(out)))
		}
		return nil
	}
	args := []string{"uninstall"}
	if !keepData {
		args = append(args, "--purge-all")
	}
	cmd := exec.CommandContext(ctx, filepath.Join(bundleRoot, "install.sh"), args...)
	cmd.Dir = bundleRoot
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("install.sh uninstall: %w (%s)", err, strings.TrimSpace(string(out)))
	}
	return nil
}

func (e *Engine) prepareUpgradeBundle(ctx context.Context, rep ReporterSink, taskID string, p Payload) (archivePath, targetVersion, workDir, bundleRoot string, err error) {
	cfg := e.current()
	initialDownloadURL := payloadStringValue(p.Extra["download_url"])
	targetVersion = payloadStringValue(p.Extra["target_version"])
	if targetVersion == "" {
		targetVersion = payloadStringValue(p.Extra["version"])
	}

	dataDir := strings.TrimSpace(cfg.DataDir)
	if dataDir == "" {
		dataDir = vfs.DefaultAgentDataDir()
	}

	workDir = install.RuntimeDownloadDir(dataDir)
	if err := os.RemoveAll(workDir); err != nil {
		return "", "", "", "", err
	}
	if err := os.MkdirAll(workDir, 0o750); err != nil {
		return "", "", "", "", err
	}

	ext := ".tar.gz"
	if runtime.GOOS == "windows" {
		ext = ".zip"
	}
	archivePath = filepath.Join(workDir, "package"+ext)
	if err := e.downloadUpgradeBundle(
		ctx,
		rep,
		taskID,
		cfg,
		initialDownloadURL,
		&targetVersion,
		archivePath,
	); err != nil {
		os.RemoveAll(workDir)
		return "", "", "", "", err
	}

	extractDir := filepath.Join(workDir, "extract")
	if err := install.ExtractArchive(ctx, archivePath, extractDir); err != nil {
		os.RemoveAll(workDir)
		return "", "", "", "", err
	}
	bundleRoot, err = install.FindBundleRoot(extractDir)
	if err != nil {
		os.RemoveAll(workDir)
		return "", "", "", "", err
	}
	if err := enroll.ValidateAgentPackage(bundleRoot, cfg.Role, targetVersion); err != nil {
		os.RemoveAll(workDir)
		return "", "", "", "", fmt.Errorf("upgrade package validation: %w", err)
	}
	return archivePath, targetVersion, workDir, bundleRoot, nil
}

func (e *Engine) downloadUpgradeBundle(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	cfg *model.AgentConfig,
	initialDownloadURL string,
	targetVersion *string,
	archivePath string,
) error {
	var lastErr error
	forbiddenRefreshed := false
	for attempt := 1; attempt <= upgradeDownloadMaxAttempts; attempt++ {
		if err := ctx.Err(); err != nil {
			return err
		}
		_ = sendProgress(ctx, rep, taskID, map[string]any{
			"phase": "download",
			"download": map[string]any{
				"state":        "resolving_release",
				"attempt":      attempt,
				"max_attempts": upgradeDownloadMaxAttempts,
			},
		})

		downloadURL := ""
		if attempt == 1 && initialDownloadURL != "" {
			downloadURL = initialDownloadURL
		} else {
			artifact, fetchErr := fetchUpgradeArtifact(ctx, cfg)
			if fetchErr != nil {
				lastErr = fetchErr
				if attempt >= upgradeDownloadMaxAttempts || !release.IsRetryableReleaseError(fetchErr) {
					return newUpgradeDownloadFailure(
						"AGENT_PACKAGE_RELEASE_RESOLUTION_FAILED",
						attempt,
						0,
						fetchErr,
					)
				}
				if waitErr := reportAndWaitUpgradeRetry(ctx, rep, taskID, attempt, "release_unavailable"); waitErr != nil {
					return waitErr
				}
				continue
			}
			if *targetVersion == "" {
				*targetVersion = strings.TrimSpace(artifact.Version)
			}
			if version := strings.TrimSpace(artifact.Version); version != "" && *targetVersion != "" && version != *targetVersion {
				return newUpgradeDownloadFailure(
					"AGENT_PACKAGE_TARGET_CHANGED",
					attempt,
					0,
					fmt.Errorf("resolved agent release changed from %s to %s", *targetVersion, version),
				)
			}
			downloadURL = artifact.DownloadURL
		}

		tracker := &upgradeDownloadTracker{}
		reporter := tracker.reporter(ctx, rep, taskID, attempt, *targetVersion)
		downloadErr := downloadUpgradeArchive(ctx, downloadURL, archivePath, reporter)
		if downloadErr == nil {
			_ = sendProgress(ctx, rep, taskID, map[string]any{
				"phase":   "download",
				"version": *targetVersion,
				"download": map[string]any{
					"state":            "completed",
					"downloaded_bytes": tracker.downloaded.Load(),
					"total_bytes":      tracker.total.Load(),
					"attempt":          attempt,
					"max_attempts":     upgradeDownloadMaxAttempts,
				},
			})
			return nil
		}
		lastErr = downloadErr
		if ctx.Err() != nil {
			return ctx.Err()
		}

		retryable := install.IsRetryableDownloadError(downloadErr)
		var httpErr *install.DownloadHTTPError
		if errors.As(downloadErr, &httpErr) && httpErr.StatusCode == http.StatusForbidden {
			// A single refresh can recover an expired signed URL. A second 403 is
			// treated as a durable authorization failure.
			retryable = !forbiddenRefreshed
			forbiddenRefreshed = true
		}
		if attempt >= upgradeDownloadMaxAttempts || !retryable {
			return newUpgradeDownloadFailure(
				finalDownloadErrorCode(downloadErr, attempt),
				attempt,
				tracker.downloaded.Load(),
				downloadErr,
			)
		}
		if waitErr := reportAndWaitUpgradeRetry(
			ctx,
			rep,
			taskID,
			attempt,
			downloadErrorReason(downloadErr),
		); waitErr != nil {
			return waitErr
		}
	}
	return newUpgradeDownloadFailure(
		"AGENT_PACKAGE_DOWNLOAD_RETRY_EXHAUSTED",
		upgradeDownloadMaxAttempts,
		0,
		lastErr,
	)
}

type upgradeDownloadTracker struct {
	downloaded atomic.Int64
	total      atomic.Int64
	lastSent   atomic.Int64
}

func (tracker *upgradeDownloadTracker) reporter(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	attempt int,
	targetVersion string,
) install.ProgressReporter {
	return func(progress install.DownloadProgress) {
		tracker.downloaded.Store(progress.DownloadedBytes)
		tracker.total.Store(progress.TotalBytes)
		now := time.Now()
		last := time.Unix(0, tracker.lastSent.Load())
		if !progress.Completed && !last.IsZero() && now.Sub(last) < upgradeDownloadProgressEvery {
			return
		}
		tracker.lastSent.Store(now.UnixNano())
		state := "downloading"
		if progress.Idle >= upgradeDownloadProgressEvery {
			state = "waiting_for_data"
		}
		_ = sendProgress(ctx, rep, taskID, map[string]any{
			"phase":   "download",
			"version": targetVersion,
			"download": map[string]any{
				"state":            state,
				"downloaded_bytes": progress.DownloadedBytes,
				"total_bytes":      progress.TotalBytes,
				"bytes_per_second": progress.BytesPerSecond,
				"elapsed_seconds":  int64(progress.Elapsed / time.Second),
				"idle_seconds":     int64(progress.Idle / time.Second),
				"attempt":          attempt,
				"max_attempts":     upgradeDownloadMaxAttempts,
			},
		})
	}
}

func reportAndWaitUpgradeRetry(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	attempt int,
	reason string,
) error {
	delay := upgradeRetryDelay(attempt)
	_ = sendProgress(ctx, rep, taskID, map[string]any{
		"phase": "download",
		"download": map[string]any{
			"state":               "retry_wait",
			"attempt":             attempt,
			"next_attempt":        attempt + 1,
			"max_attempts":        upgradeDownloadMaxAttempts,
			"retry_after_seconds": int64(delay / time.Second),
			"reason":              reason,
		},
	})
	return waitUpgradeRetry(ctx, delay)
}

func upgradeRetryDelay(attempt int) time.Duration {
	index := attempt - 1
	if index < 0 || index >= len(upgradeRetryDelays) {
		return 0
	}
	base := upgradeRetryDelays[index]
	jitterLimit := 3 * time.Second
	if attempt > 1 {
		jitterLimit = 5 * time.Second
	}
	return base + time.Duration(rand.Int64N(int64(jitterLimit)+1))
}

func waitWithContext(ctx context.Context, delay time.Duration) error {
	if delay <= 0 {
		return nil
	}
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func newUpgradeDownloadFailure(code string, attempts int, downloaded int64, err error) error {
	if err == nil {
		err = errors.New("agent package download failed")
	}
	result := map[string]any{
		"phase":               "download",
		"error_code":          code,
		"attempts":            attempts,
		"max_attempts":        upgradeDownloadMaxAttempts,
		"downloaded_bytes":    downloaded,
		"old_agent_unchanged": true,
	}
	if code == "AGENT_PACKAGE_DOWNLOAD_RETRY_EXHAUSTED" {
		result["last_error_code"] = specificDownloadErrorCode(err)
	}
	return &upgradePreparationError{
		err:    err,
		result: result,
	}
}

func finalDownloadErrorCode(err error, attempts int) string {
	if attempts >= upgradeDownloadMaxAttempts && install.IsRetryableDownloadError(err) {
		return "AGENT_PACKAGE_DOWNLOAD_RETRY_EXHAUSTED"
	}
	return specificDownloadErrorCode(err)
}

func specificDownloadErrorCode(err error) string {
	if errors.Is(err, install.ErrDownloadNoProgress) {
		return "AGENT_PACKAGE_DOWNLOAD_NO_PROGRESS"
	}
	var httpErr *install.DownloadHTTPError
	if errors.As(err, &httpErr) {
		return "AGENT_PACKAGE_DOWNLOAD_HTTP_ERROR"
	}
	return "AGENT_PACKAGE_DOWNLOAD_STREAM_FAILED"
}

func downloadErrorReason(err error) string {
	if errors.Is(err, install.ErrDownloadNoProgress) {
		return "no_progress"
	}
	var httpErr *install.DownloadHTTPError
	if errors.As(err, &httpErr) {
		return fmt.Sprintf("http_%d", httpErr.StatusCode)
	}
	if errors.Is(err, install.ErrDownloadSizeMismatch) || errors.Is(err, io.ErrUnexpectedEOF) {
		return "truncated"
	}
	return "network_error"
}
