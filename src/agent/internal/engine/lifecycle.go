package engine

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/install"
	"hyperfilelens/agent/internal/platform/release"
	"hyperfilelens/agent/internal/platform/tlsclient"
	"hyperfilelens/agent/internal/platform/vfs"
	"hyperfilelens/agent/internal/selfupdate"
)

func (e *Engine) runAgentUpgrade(ctx context.Context, rep ReporterSink, taskID string, p Payload) (string, map[string]any, string) {
	if e.current().InstallationMode == model.InstallationModeAccount {
		return "failed", nil, "specified-user continuous protection requires administrator authorization for upgrade; run the generated local administrator command"
	}
	archivePath, targetVersion, workDir, err := e.prepareUpgradeBundle(ctx, rep, taskID, p)
	if err != nil {
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

	_ = sendProgress(ctx, rep, taskID, map[string]any{
		"phase":       "upgrade",
		"mode":        "local_detached",
		"upgrade_log": upgradeLog,
	})
	if err := install.ScheduleDetachedUpgrade(
		installDir,
		stagedArchive,
		logDir,
		cfg.InstallationMode == model.InstallationModeUser || cfg.InstallationMode == model.InstallationModeUserContinuous,
	); err != nil {
		slog.Warn("detached upgrade schedule failed", "err", err, "upgrade_log", upgradeLog)
		_ = os.RemoveAll(filepath.Dir(stagedArchive))
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

func (e *Engine) prepareUpgradeBundle(ctx context.Context, rep ReporterSink, taskID string, p Payload) (archivePath, targetVersion, workDir string, err error) {
	_ = sendProgress(ctx, rep, taskID, map[string]any{"phase": "resolve_release"})

	cfg := e.current()
	downloadURL := payloadStringValue(p.Extra["download_url"])
	targetVersion = payloadStringValue(p.Extra["version"])
	if downloadURL == "" {
		downloadURL, targetVersion, err = release.FetchDownloadURL(ctx, cfg)
		if err != nil {
			return "", "", "", err
		}
	}

	dataDir := strings.TrimSpace(cfg.DataDir)
	if dataDir == "" {
		dataDir = vfs.DefaultAgentDataDir()
	}

	workDir = install.RuntimeDownloadDir(dataDir)
	if err := os.RemoveAll(workDir); err != nil {
		return "", "", "", err
	}
	if err := os.MkdirAll(workDir, 0o750); err != nil {
		return "", "", "", err
	}

	ext := ".tar.gz"
	if runtime.GOOS == "windows" {
		ext = ".zip"
	}
	archivePath = filepath.Join(workDir, "package"+ext)
	_ = sendProgress(ctx, rep, taskID, map[string]any{
		"phase":   "download",
		"version": targetVersion,
	})
	if err := install.DownloadURL(ctx, downloadURL, archivePath); err != nil {
		os.RemoveAll(workDir)
		return "", "", "", err
	}

	extractDir := filepath.Join(workDir, "extract")
	if err := install.ExtractArchive(ctx, archivePath, extractDir); err != nil {
		os.RemoveAll(workDir)
		return "", "", "", err
	}
	if _, err := install.FindBundleRoot(extractDir); err != nil {
		os.RemoveAll(workDir)
		return "", "", "", err
	}
	return archivePath, targetVersion, workDir, nil
}
