package enroll

import (
	"context"
	"fmt"
	"strings"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/release"
)

// ReinstallAction describes what to do when agent binaries already exist.
type ReinstallAction string

const (
	ActionFreshInstall    ReinstallAction = "fresh_install"
	ActionAlreadyEnrolled ReinstallAction = "already_enrolled"
	ActionRepair          ReinstallAction = "repair"
	ActionUpgrade         ReinstallAction = "upgrade"
	ActionReinstall       ReinstallAction = "reinstall"
	ActionRebind          ReinstallAction = "rebind"
	ActionCrossOrg        ReinstallAction = "cross_org"
)

// ReinstallPlan is the resolved path for an enrollment run on an existing install.
type ReinstallPlan struct {
	Action         ReinstallAction
	NeedsConfirm   bool
	ConfirmMessage string
	ReleaseVersion string
	DownloadURL    string
}

// PlanReinstall decides how to handle enrollment when the agent is already installed.
func PlanReinstall(ctx context.Context, cfg Config, state InstallState) (ReinstallPlan, error) {
	return PlanInstall(ctx, cfg, state, InstallModeAuto)
}

// PlanInstall resolves automatic or explicitly requested existing-install behavior.
func PlanInstall(
	ctx context.Context,
	cfg Config,
	state InstallState,
	mode InstallMode,
) (ReinstallPlan, error) {
	if state.OrgKey != "" && !strings.EqualFold(state.OrgKey, cfg.OrgKey) {
		return ReinstallPlan{Action: ActionCrossOrg}, nil
	}
	if state.Role != "" && !strings.EqualFold(state.Role, string(cfg.NodeRole)) {
		return ReinstallPlan{}, fmt.Errorf(
			"this host is already installed as %s; uninstall that role before installing %s",
			roleDisplayName(model.Role(state.Role)),
			roleDisplayName(cfg.NodeRole, cfg.GatewayScope),
		)
	}
	if state.InstallationMode != "" &&
		!strings.EqualFold(state.InstallationMode, string(cfg.InstallationMode)) {
		return ReinstallPlan{}, fmt.Errorf(
			"this host is already installed in %s mode; uninstall that local installation before switching to %s mode",
			state.InstallationMode,
			cfg.InstallationMode,
		)
	}
	if mode != InstallModeAuto && !state.Installed {
		return ReinstallPlan{}, fmt.Errorf("--%s requires an existing HyperFileLens Agent installation", mode)
	}
	if mode == InstallModeRepair {
		return ReinstallPlan{
			Action:         ActionRepair,
			NeedsConfirm:   true,
			ConfirmMessage: "Repair the Agent configuration and restart its service?",
		}, nil
	}

	healthy := state.ServiceHealthy()
	hasNode := strings.TrimSpace(state.NodeID) != ""

	dl, releaseVer, releaseErr := release.FetchDownloadURL(ctx, cfg.AgentConfig())
	// A legacy machine-wide installation is a supported upgrade boundary. Do
	// not classify a healthy, same-version Gateway as already enrolled: the
	// installer must migrate agent.env/agent.db and install the unified paths
	// before Gateway sidecar setup reads the node identity.
	if legacyLayoutRequiresMigration(state, mode) {
		releaseLabel := strings.TrimSpace(releaseVer)
		if releaseLabel == "" {
			releaseLabel = "the current console release"
		}
		return ReinstallPlan{
			Action:         ActionUpgrade,
			NeedsConfirm:   true,
			ReleaseVersion: releaseVer,
			DownloadURL:    dl,
			ConfirmMessage: fmt.Sprintf(
				"Migrate the existing Agent to the unified installation layout using %s? The service will be interrupted briefly.",
				releaseLabel,
			),
		}, nil
	}
	if mode == InstallModeUpgrade || mode == InstallModeReinstall {
		if releaseErr != nil {
			return ReinstallPlan{}, releaseErr
		}
		action := ActionUpgrade
		verb := "Upgrade"
		if mode == InstallModeReinstall {
			action = ActionReinstall
			verb = "Reinstall"
		}
		releaseLabel := strings.TrimSpace(releaseVer)
		if releaseLabel == "" {
			releaseLabel = "selected by the console"
		}
		return ReinstallPlan{
			Action:         action,
			NeedsConfirm:   true,
			ReleaseVersion: releaseVer,
			DownloadURL:    dl,
			ConfirmMessage: fmt.Sprintf(
				"%s the Agent using console release %s? The service will be interrupted briefly.",
				verb,
				releaseLabel,
			),
		}, nil
	}
	_ = releaseErr

	if hasNode && healthy {
		if releaseVer != "" && state.Version != "" && versionGreater(releaseVer, state.Version) {
			return ReinstallPlan{
				Action:         ActionUpgrade,
				NeedsConfirm:   true,
				ReleaseVersion: releaseVer,
				DownloadURL:    dl,
				ConfirmMessage: fmt.Sprintf(
					"Version %s is installed, but the console offers version %s. Upgrade may briefly interrupt backups.",
					state.Version, releaseVer,
				),
			}, nil
		}
		return ReinstallPlan{Action: ActionAlreadyEnrolled}, nil
	}

	if hasNode && !healthy {
		if releaseVer != "" && state.Version != "" && versionGreater(releaseVer, state.Version) {
			return ReinstallPlan{
				Action:         ActionUpgrade,
				NeedsConfirm:   true,
				ReleaseVersion: releaseVer,
				DownloadURL:    dl,
				ConfirmMessage: fmt.Sprintf(
					"Node %s is enrolled, but the service is %s. Upgrade %s and restart the agent?",
					state.NodeID, state.Service, versionLabel(state.Version, releaseVer),
				),
			}, nil
		}
		return ReinstallPlan{
			Action:       ActionRepair,
			NeedsConfirm: true,
			ConfirmMessage: fmt.Sprintf(
				"Node %s is enrolled, but the service is %s. Restart the agent service and reconnect to the console?",
				state.NodeID, state.Service,
			),
		}, nil
	}

	return ReinstallPlan{
		Action:         ActionRebind,
		NeedsConfirm:   true,
		ConfirmMessage: "The agent is installed but not registered with the console. Bind this host now?",
	}, nil
}

func legacyLayoutRequiresMigration(state InstallState, mode InstallMode) bool {
	return state.Installed && state.LegacyLayout && mode == InstallModeAuto
}
