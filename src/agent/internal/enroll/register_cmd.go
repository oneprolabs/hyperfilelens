package enroll

import (
	"context"
	"fmt"
	"os"
	"strings"
	"time"

	"hyperfilelens/agent/internal/enrollmentclient"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/install"
)

// RunRegister sends enrollment heartbeat and persists node_id (install must already exist).
func RunRegister(ctx context.Context, opts InstallOptions) error {
	if opts.Invalid != "" || opts.Mode != InstallModeAuto || opts.PurgeAll {
		abortInstall(
			"Initialization",
			"register accepts only --yes and output-format options",
			2,
			"HFL-INSTALL-OPTIONS",
		)
	}
	cfg, err := LoadConfigFromEnv()
	if err != nil {
		logFail(err.Error(), 2)
	}
	existingNodeCredential := installedNodeCredential()
	envReport, err := RunEnvironmentChecks(ctx, cfg)
	if err != nil {
		return err
	}
	cfg.InstallationID = envReport.InstallationID

	state := envReport.Existing
	if !state.Installed {
		logFail("The agent is not installed. Run hfl-enroll install first.", 2)
	}

	identityAction, err := validateExistingInstallIdentity(cfg, state)
	if err != nil {
		logFail(err.Error(), 3)
	}
	if identityAction == ActionCrossOrg {
		logFail("This agent belongs to a different organization. Uninstall it first, then try again.", 1)
	}
	if confirmMessage := registerConfirmationMessage(state); confirmMessage != "" {
		if err := confirmAction(confirmMessage, opts.AutoYes); err != nil {
			logFail(err.Error(), 1)
		}
	}
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
	if session.GatewayScope != "" {
		if cfg.NodeRole == model.RoleGateway && gatewayScopeMismatch(state.GatewayScope, session.GatewayScope) {
			logFail(
				fmt.Sprintf(
					"this Gateway is configured for scope %q, but this enrollment command targets scope %q; uninstall the existing Gateway before changing its scope",
					state.GatewayScope,
					session.GatewayScope,
				),
				3,
			)
		}
		cfg.GatewayScope = session.GatewayScope
		_ = os.Setenv("HFL_GATEWAY_SCOPE", session.GatewayScope)
	}

	snapshot, err := captureEnrollmentEnv()
	if err != nil {
		logFail("Existing Agent configuration could not be preserved: "+err.Error(), 3)
	}
	if err := refreshAgentConfig(cfg); err != nil {
		logFail(err.Error(), 3)
	}
	committed := false
	rememberIssuedCredential := func(credential string) {
		if !snapshot.exists {
			recovery, captureErr := captureEnrollmentEnv()
			if captureErr != nil {
				logWarn("Credential recovery configuration could not be captured: " + captureErr.Error())
				return
			}
			snapshot = recovery
		}
		snapshot = snapshot.withNodeCredential(credential)
		snapshot = snapshot.withInstallationID(cfg.InstallationID)
	}
	defer func() {
		if committed {
			return
		}
		if restoreErr := snapshot.restore(); restoreErr != nil {
			logWarn("Original Agent configuration could not be restored: " + restoreErr.Error())
		}
	}()

	agentVer, verErr := InstalledAgentVersion(ctx)
	if verErr != nil {
		logWarn(verErr.Error())
	}

	return finishEnrollment(
		ctx,
		cfg,
		agentVer,
		existingNodeCredential,
		rememberIssuedCredential,
		func() {
			committed = true
			sessionCompleted = true
		},
	)
}

func registerConfirmationMessage(state InstallState) string {
	if strings.TrimSpace(state.NodeID) == "" {
		return "The agent is installed but not registered with the console. Bind this host now?"
	}
	if !state.ServiceHealthy() {
		return fmt.Sprintf(
			"Node %s is enrolled, but the service is %s. Restart the agent service and reconnect to the console?",
			state.NodeID,
			state.Service,
		)
	}
	return ""
}

// RunStatus prints installed agent enrollment status.
func RunStatus(ctx context.Context) error {
	envPath := EnvFilePath()
	state := DetectInstallState()
	installDir := install.DefaultInstallDir()

	logInfo("HyperFileLens agent status report.")
	if state.Installed {
		if state.Version != "" {
			logOK(fmt.Sprintf("Agent is installed (version %s).", state.Version))
		} else {
			logOK("Agent is installed.")
		}
	} else {
		logInfo("Agent is not installed.")
	}
	if state.NodeID != "" {
		logInfo(fmt.Sprintf("Node id: %s.", state.NodeID))
	} else {
		logInfo("Node id: not registered.")
	}
	if state.Service != "" {
		logInfo(fmt.Sprintf("Service: %s.", state.Service))
	}
	if state.OrgKey != "" {
		logInfo(fmt.Sprintf("Organization: %s.", state.OrgKey))
	}
	logInfo(fmt.Sprintf("Config: %s.", envPath))
	logInfo(fmt.Sprintf("Install directory: %s.", installDir))
	return nil
}
