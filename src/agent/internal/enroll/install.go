package enroll

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"hyperfilelens/agent/internal/enrollmentclient"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/install"
	"hyperfilelens/agent/internal/platform/release"
)

// RunInstall performs the full console enrollment pipeline.
func RunInstall(ctx context.Context, opts InstallOptions) error {
	if opts.Invalid != "" {
		abortInstall("Initialization", opts.Invalid, 2, "HFL-INSTALL-OPTIONS")
	}
	if opts.Mode == InstallModeUninstall {
		LoadInstalledCommandEnv()
	}
	cfg, err := LoadConfigFromEnv()
	if err != nil {
		abortInstall("Initialization", err.Error(), 2, "HFL-INSTALL-CONFIG")
	}
	if opts.Mode == InstallModeUninstall {
		return runExplicitUninstall(ctx, cfg, opts)
	}
	existingNodeCredential := installedNodeCredential()

	envReport, err := RunEnvironmentChecks(ctx, cfg)
	if err != nil {
		return err
	}
	cfg.InstallationID = envReport.InstallationID

	state := envReport.Existing
	agentVer := state.Version
	plan := ReinstallPlan{Action: ActionFreshInstall}
	if state.Installed {
		plan, err = PlanInstall(ctx, cfg, state, opts.Mode)
		if err != nil {
			logFail(err.Error(), 3)
		}
	} else if opts.Mode != InstallModeAuto {
		logFail(fmt.Sprintf("--%s requires an existing HyperFileLens Agent installation", opts.Mode), 2)
	}

	switch plan.Action {
	case ActionCrossOrg:
		logFail(fmt.Sprintf(
			"This agent belongs to organization %q, but this enrollment link is for %q. Uninstall the agent first, then try again.",
			state.OrgKey, cfg.OrgKey,
		), 1)
	case ActionAlreadyEnrolled:
		if ver, verErr := InstalledAgentVersion(ctx); verErr == nil && ver != "" {
			agentVer = ver
		}
		info := summaryFromState(cfg.APIBase, state.NodeID, agentVer, state.Service)
		info.Role = roleDisplayName(cfg.NodeRole, cfg.GatewayScope)
		printAlreadyEnrolled(info)
		return nil
	}
	if plan.NeedsConfirm {
		if err := confirmAction(plan.ConfirmMessage, opts.AutoYes); err != nil {
			logFail(err.Error(), 1)
		}
	}
	printPhase(installActionPhase(plan.Action))
	commitInstallLog()
	if err := persistInstallationID(cfg.InstallationID); err != nil {
		logFail("Failed to persist the installation identity: "+err.Error(), 3)
	}

	session, err := enrollmentclient.OpenInstallationSession(
		ctx,
		cfg.AgentConfig(),
		cfg.InstallationID,
	)
	if err != nil {
		logFail("Installation session could not be started: "+err.Error(), 2)
	}
	cfg.NodeToken = session.Secret
	sessionCompleted := false
	configCommitted := false
	var stagedConfig enrollmentEnvSnapshot
	configStaged := false
	restartAfterConfigRestore := false
	defer func() {
		if sessionCompleted {
			return
		}
		releaseCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 15*time.Second)
		defer cancel()
		if releaseErr := enrollmentclient.ReleaseInstallationSession(
			releaseCtx,
			cfg.AgentConfig(),
			cfg.InstallationID,
		); releaseErr != nil {
			logWarn("Installation session release failed: " + releaseErr.Error())
		}
	}()
	defer func() {
		if !configStaged || configCommitted {
			return
		}
		if restoreErr := stagedConfig.restore(); restoreErr != nil {
			logWarn("Original Agent configuration could not be restored: " + restoreErr.Error())
			return
		}
		logOK("Original Agent configuration was restored.")
		if restartAfterConfigRestore {
			restartCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 30*time.Second)
			defer cancel()
			if restartErr := RestartInstalledService(restartCtx); restartErr != nil {
				logWarn("Agent service could not be restarted with the restored configuration: " + restartErr.Error())
				return
			}
			logOK("Agent service was restarted with the restored configuration.")
		}
	}()
	freshInstallAttempted := false
	defer func() {
		if plan.Action != ActionFreshInstall || !freshInstallAttempted || sessionCompleted {
			return
		}
		logStep("Rolling back the incomplete Agent installation.")
		if _, statErr := os.Stat(filepath.Join(install.DefaultInstallDir(), installerScriptName())); statErr != nil {
			logSkip("No installed Agent bundle was available for rollback.")
			return
		}
		rollbackCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 2*time.Minute)
		defer cancel()
		// Keep the installation identity so a retry reuses the same console record.
		if rollbackErr := install.RunRollbackIncompleteInstall(
			rollbackCtx,
			install.DefaultInstallDir(),
		); rollbackErr != nil {
			logWarn("Rollback did not complete: " + rollbackErr.Error())
			return
		}
		logOK("Incomplete Agent installation was removed; installation identity and Agent data were preserved.")
	}()
	if session.GatewayScope != "" {
		cfg.GatewayScope = session.GatewayScope
		_ = os.Setenv("HFL_GATEWAY_SCOPE", session.GatewayScope)
	}
	stageExistingConfig := func() {
		stagedConfig, err = captureEnrollmentEnv()
		if err != nil {
			logFail("Existing Agent configuration could not be preserved: "+err.Error(), 3)
		}
		if err := refreshAgentConfig(cfg); err != nil {
			logFail(err.Error(), 3)
		}
		configStaged = true
	}
	markEnrollmentComplete := func() {
		sessionCompleted = true
		configCommitted = true
	}
	rememberIssuedCredential := func(credential string) {
		if configStaged {
			if !stagedConfig.exists {
				recovery, captureErr := captureEnrollmentEnv()
				if captureErr != nil {
					logWarn("Credential recovery configuration could not be captured: " + captureErr.Error())
					return
				}
				stagedConfig = recovery
			}
			stagedConfig = stagedConfig.withNodeCredential(credential)
			stagedConfig = stagedConfig.withInstallationID(cfg.InstallationID)
		}
	}

	if plan.Action == ActionFreshInstall {
		freshInstallAttempted = true
		if err := installAgentPackage(ctx, cfg, &agentVer); err != nil {
			logFail(err.Error(), 3)
		}
		err = finishEnrollment(
			ctx,
			cfg,
			agentVer,
			existingNodeCredential,
			nil,
			markEnrollmentComplete,
		)
		return err
	}

	switch plan.Action {
	case ActionRepair:
		stageExistingConfig()
		if ver, verErr := InstalledAgentVersion(ctx); verErr == nil && ver != "" {
			agentVer = ver
		}
		err = finishEnrollment(
			ctx,
			cfg,
			agentVer,
			existingNodeCredential,
			rememberIssuedCredential,
			markEnrollmentComplete,
		)
		return err

	case ActionUpgrade, ActionReinstall:
		stageExistingConfig()
		restartAfterConfigRestore = true
		dl := plan.DownloadURL
		releaseVer := plan.ReleaseVersion
		if dl == "" {
			var fetchErr error
			dl, releaseVer, fetchErr = release.FetchDownloadURLWithRetry(ctx, cfg.AgentConfig(), func(attempt, max int, retryErr error) {
				logWarn(fmt.Sprintf("Console release API unavailable (attempt %d/%d): %v", attempt, max, retryErr))
			})
			if fetchErr != nil {
				logFail("Failed to resolve the agent release: "+fetchErr.Error(), 3)
			}
		}
		if err := upgradeAgentPackage(ctx, cfg, dl, releaseVer); err != nil {
			logFail(err.Error(), 3)
		}
		if ver, verErr := InstalledAgentVersion(ctx); verErr == nil && ver != "" {
			agentVer = ver
		} else if releaseVer != "" {
			agentVer = releaseVer
		}
		err = finishEnrollment(
			ctx,
			cfg,
			agentVer,
			existingNodeCredential,
			rememberIssuedCredential,
			markEnrollmentComplete,
		)
		return err

	case ActionRebind:
		stageExistingConfig()
		if ver, verErr := InstalledAgentVersion(ctx); verErr == nil && ver != "" {
			agentVer = ver
		}
		err = finishEnrollment(
			ctx,
			cfg,
			agentVer,
			existingNodeCredential,
			rememberIssuedCredential,
			markEnrollmentComplete,
		)
		return err
	}

	logFail("Unsupported reinstall action.", 3)
	return nil
}

func runExplicitUninstall(ctx context.Context, cfg Config, opts InstallOptions) error {
	state := DetectInstallState()
	if !state.Installed {
		logSkip("No HyperFileLens Agent installation was found.")
		return nil
	}
	if state.OrgKey != "" && !strings.EqualFold(state.OrgKey, cfg.OrgKey) {
		abortInstall(
			"Preflight checks",
			"This Agent belongs to a different organization. Use its original installation environment to uninstall it.",
			1,
			"HFL-UNINSTALL-ORG",
		)
	}
	printUninstallContext(cfg.APIBase, cfg.OrgKey, cfg.NodeRole, state, opts.PurgeAll)
	printPhase("Preflight checks")
	logOK("Installed Agent ownership was verified.")
	message := "Uninstall the HyperFileLens Agent and preserve its data directory?"
	if opts.PurgeAll {
		message = "Uninstall the HyperFileLens Agent and permanently remove its managed data?"
	}
	if err := confirmAction(message, opts.AutoYes); err != nil {
		abortInstall("Preflight checks", err.Error(), 1, "HFL-UNINSTALL-CONFIRM")
	}
	logOK("Uninstall request was confirmed.")
	printPhase("Uninstalling")
	var uninstallErr error
	if cfg.NodeRole == model.RoleGateway {
		uninstallErr = runGatewayUninstall(ctx, opts.PurgeAll, false)
	} else {
		logStep("Removing the HyperFileLens Agent.")
		uninstallErr = install.RunUninstallWithDataPolicy(
			ctx,
			install.DefaultInstallDir(),
			!opts.PurgeAll,
		)
	}
	if uninstallErr != nil {
		return uninstallErr
	}
	logOK("HyperFileLens Agent uninstall completed.")
	printPhase("Verifying")
	logOK("Agent service and installed files were removed.")
	printUninstallSuccess(state, opts.PurgeAll)
	return nil
}

func validateInstalledAgent(ctx context.Context) error {
	if _, err := InstalledAgentVersion(ctx); err != nil {
		return fmt.Errorf("installed agent verification failed: %w", err)
	}
	return nil
}

func refreshAgentConfig(cfg Config) error {
	logStep("Refreshing agent configuration.")
	// Keep durable credentials on disk. The installation session secret remains
	// in-memory for registration/download calls while the Agent may still run.
	if err := syncEnrollmentConsoleSettings(cfg); err != nil {
		return err
	}
	logOK("Agent configuration was refreshed successfully.")
	return nil
}

func finishEnrollment(
	ctx context.Context,
	cfg Config,
	agentVer string,
	existingNodeCredential string,
	onCredentialIssued func(string),
	onRegistered func(),
) error {
	if err := validateInstalledAgent(ctx); err != nil {
		logFail(err.Error(), 3)
	}
	logOK("Agent binaries were verified successfully.")

	logStep("Registering node with the console.")
	agentCfg := cfg.AgentConfig()
	// Enrollment sessions bind to the identity for this installation lifetime.
	// Omitting a stale node id keeps repair, upgrade, and rebind on the
	// enrollment path; regular Agent heartbeats use their persisted node id.
	agentCfg.NodeID = ""
	if err := WriteInstallationID(EnvFilePath(), cfg.InstallationID); err != nil {
		logFail("Failed to persist the installation identity: "+err.Error(), 5)
	}
	registration, err := enrollmentclient.RegisterNodeHTTP(
		ctx,
		agentCfg,
		agentVer,
		existingNodeCredential,
	)
	nodeID := registration.NodeID
	if err != nil {
		if enrollmentclient.IsInvalidEnrollmentToken(err) {
			abortInstall(
				"Registration",
				"The installation authorization expired or was revoked. Generate a new installation command and try again.",
				5,
				"HFL-REGISTER-005",
			)
		} else {
			logFail("Node registration failed: "+err.Error(), 5)
		}
	} else {
		logOK(fmt.Sprintf("Node registered successfully (ID %s).", nodeID))
	}

	envPath := EnvFilePath()
	if registration.CredentialReused {
		if strings.TrimSpace(existingNodeCredential) == "" {
			logFail("Console reported credential reuse, but no existing node credential is available", 5)
		}
		if err := WriteNodeCredential(envPath, existingNodeCredential); err != nil {
			logFail("Failed to restore the existing node credential: "+err.Error(), 5)
		}
		cfg.NodeToken = existingNodeCredential
		agentCfg.NodeToken = existingNodeCredential
	} else if registration.NodeCredential != "" {
		if onCredentialIssued != nil {
			onCredentialIssued(registration.NodeCredential)
		}
		if err := WriteNodeCredential(envPath, registration.NodeCredential); err != nil {
			logFail("Failed to persist the node credential: "+err.Error(), 5)
		}
		cfg.NodeToken = registration.NodeCredential
		agentCfg.NodeToken = registration.NodeCredential
	}
	if err := WriteNodeID(envPath, nodeID); err != nil {
		logFail("Failed to update agent.env: "+err.Error(), 5)
	}
	if onRegistered != nil {
		onRegistered()
	}

	logStep("Starting the agent service.")
	if err := StartInstalledService(ctx); err != nil {
		logFail("Agent service start failed: "+err.Error(), 6)
	}

	printPhase("Verifying")
	service := serviceState(ctx)
	if service == "" {
		service = "active"
	}
	logOK(fmt.Sprintf("Agent service is %s.", service))
	logStep("Waiting for the Agent to come online.")
	if err := enrollmentclient.WaitNodeOnline(ctx, agentCfg, nodeID, 30*time.Second); err != nil {
		abortInstall(
			"Post-install verification",
			"The Agent could not establish a control-plane WebSocket connection: "+err.Error(),
			3,
			"HFL-VERIFY-003",
		)
	}
	logOK("Node is online in HyperFileLens")

	info := summaryFromState(cfg.APIBase, nodeID, agentVer, service)
	info.Role = roleDisplayName(cfg.NodeRole, cfg.GatewayScope)
	if cfg.NodeRole == model.RoleGateway {
		return nil
	}
	printEnrollmentSuccess(info)
	return nil
}

func installActionPhase(action ReinstallAction) string {
	switch action {
	case ActionUpgrade:
		return "Upgrading Agent"
	case ActionRepair:
		return "Repairing Agent"
	case ActionReinstall:
		return "Reinstalling Agent"
	case ActionRebind:
		return "Registering Agent"
	default:
		return "Installing Agent"
	}
}

func installerScriptName() string {
	if runtime.GOOS == "windows" {
		return "install.ps1"
	}
	return "install.sh"
}

func installAgentPackage(ctx context.Context, cfg Config, agentVer *string) error {
	dl, releaseVersion, err := resolveRelease(ctx, cfg)
	if err != nil {
		return err
	}
	if releaseVersion != "" {
		*agentVer = releaseVersion
		logStep(fmt.Sprintf("Downloading agent version %s.", releaseVersion))
	} else {
		logStep("Downloading the agent package.")
	}

	if filename := safeDownloadFilename(dl); filename != "" {
		logInfo("Selected package: " + filename)
	}
	archivePath, cleanup, err := downloadReleaseArchive(
		ctx,
		dl,
		agentPackageLabel(cfg.NodeRole, cfg.GatewayScope),
	)
	if err != nil {
		return err
	}
	defer cleanup()

	label := agentPackageLabel(cfg.NodeRole, cfg.GatewayScope)
	logStep("Extracting " + label + ".")
	bundleRoot, err := extractReleaseBundle(ctx, archivePath)
	if err != nil {
		return err
	}
	logOK(label + " extracted")
	logStep("Verifying " + label + ".")
	if err := validateAgentPackage(bundleRoot, cfg.NodeRole, releaseVersion); err != nil {
		return fmt.Errorf("Agent package validation failed: %w", err)
	}
	logOK(label + " verified")

	logStep("Installing agent binaries and service.")
	if err := RunBundleInstall(ctx, bundleRoot, cfg); err != nil {
		return err
	}
	logOK("Agent files and service configuration were installed successfully.")

	if ver, verErr := InstalledAgentVersion(ctx); verErr == nil && ver != "" {
		*agentVer = ver
	}
	return nil
}

func upgradeAgentPackage(ctx context.Context, cfg Config, downloadURL, releaseVersion string) error {
	if releaseVersion != "" {
		logStep(fmt.Sprintf("Downloading agent version %s.", releaseVersion))
	} else {
		logStep("Downloading the agent package.")
	}

	if filename := safeDownloadFilename(downloadURL); filename != "" {
		logInfo("Selected package: " + filename)
	}
	archivePath, cleanup, err := downloadReleaseArchive(
		ctx,
		downloadURL,
		agentPackageLabel(cfg.NodeRole, cfg.GatewayScope),
	)
	if err != nil {
		return err
	}
	defer cleanup()
	label := agentPackageLabel(cfg.NodeRole, cfg.GatewayScope)
	logStep("Extracting " + label + ".")
	bundleRoot, err := extractReleaseBundle(ctx, archivePath)
	if err != nil {
		return err
	}
	logOK(label + " extracted")
	logStep("Verifying " + label + ".")
	if err := validateAgentPackage(bundleRoot, cfg.NodeRole, releaseVersion); err != nil {
		return fmt.Errorf("Agent package validation failed: %w", err)
	}
	logOK(label + " verified")

	logStep("Upgrading agent binaries.")
	if err := RunBundleUpgrade(ctx, archivePath); err != nil {
		return err
	}
	logOK("Agent binaries were upgraded successfully.")
	return nil
}

func resolveRelease(ctx context.Context, cfg Config) (downloadURL, version string, err error) {
	downloadURL, version, err = release.FetchDownloadURLWithRetry(ctx, cfg.AgentConfig(), func(attempt, max int, retryErr error) {
		logWarn(fmt.Sprintf("Console release API unavailable (attempt %d/%d): %v", attempt, max, retryErr))
	})
	if err != nil {
		return "", "", fmt.Errorf("Failed to resolve the agent release: %w", err)
	}
	return downloadURL, version, nil
}

func downloadReleaseArchive(
	ctx context.Context,
	downloadURL string,
	label string,
) (archivePath string, cleanup func(), err error) {
	workDir, err := os.MkdirTemp("", "hfl-enroll-")
	if err != nil {
		return "", nil, fmt.Errorf("temp dir: %w", err)
	}
	cleanup = func() { _ = os.RemoveAll(workDir) }

	ext := ".tar.gz"
	if runtime.GOOS == "windows" {
		ext = ".zip"
	}
	archivePath = filepath.Join(workDir, "package"+ext)
	if err := downloadWithProgress(ctx, downloadURL, archivePath, label); err != nil {
		cleanup()
		return "", nil, fmt.Errorf("Download failed: %w", err)
	}
	return archivePath, cleanup, nil
}

func extractReleaseBundle(ctx context.Context, archivePath string) (string, error) {
	workDir := filepath.Dir(archivePath)
	extractDir := filepath.Join(workDir, "extract")
	if err := install.ExtractArchive(ctx, archivePath, extractDir); err != nil {
		return "", fmt.Errorf("Extract failed: %w", err)
	}
	bundleRoot, err := install.FindBundleRoot(extractDir)
	if err != nil {
		return "", fmt.Errorf("Invalid distribution archive: %w", err)
	}
	return bundleRoot, nil
}

func runtimeArch() string {
	if runtime.GOARCH == "arm64" {
		return "arm64"
	}
	return "amd64"
}
