package enroll

import (
	"context"
	"fmt"
	"net"
	"net/url"
	"strings"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/release"
)

// ReinstallAction describes what to do when agent binaries already exist.
type ReinstallAction string

const (
	ActionFreshInstall    ReinstallAction = "fresh_install"
	ActionAlreadyEnrolled ReinstallAction = "already_enrolled" // Deprecated: reconcile instead.
	ActionRepair          ReinstallAction = "repair"
	ActionUpgrade         ReinstallAction = "upgrade"
	ActionReinstall       ReinstallAction = "reinstall"
	ActionRebind          ReinstallAction = "rebind"
	ActionReconcile       ReinstallAction = "reconcile"
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
	identityAction, err := validateExistingInstallIdentity(cfg, state)
	if err != nil {
		return ReinstallPlan{}, err
	}
	if identityAction != "" {
		return ReinstallPlan{Action: identityAction}, nil
	}

	hasNode := strings.TrimSpace(state.NodeID) != ""
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
		if state.Version != "" && releaseVer != "" && versionGreater(state.Version, releaseVer) {
			if healthy {
				return ReinstallPlan{Action: ActionReconcile}, nil
			}
			return ReinstallPlan{
				Action:       ActionRepair,
				NeedsConfirm: true,
				ConfirmMessage: fmt.Sprintf(
					"Version %s is newer than the console release %s. Keep the installed version and restart the Agent service?",
					state.Version, releaseVer,
				),
			}, nil
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
		return ReinstallPlan{Action: ActionReconcile}, nil
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

func validateExistingInstallIdentity(cfg Config, state InstallState) (ReinstallAction, error) {
	if state.OrgKey != "" && !strings.EqualFold(state.OrgKey, cfg.OrgKey) {
		return ActionCrossOrg, nil
	}
	if state.Role != "" && !strings.EqualFold(state.Role, string(cfg.NodeRole)) {
		return "", fmt.Errorf(
			"this host is already installed as %s; uninstall that role before installing %s",
			roleDisplayName(model.Role(state.Role)),
			roleDisplayName(cfg.NodeRole, cfg.GatewayScope),
		)
	}
	if state.InstallationMode != "" &&
		!strings.EqualFold(state.InstallationMode, string(cfg.InstallationMode)) {
		return "", fmt.Errorf(
			"this host is already installed in %s mode; uninstall that local installation before switching to %s mode",
			state.InstallationMode,
			cfg.InstallationMode,
		)
	}
	if cfg.NodeRole == model.RoleGateway && gatewayScopeMismatch(state.GatewayScope, cfg.GatewayScope) {
		return "", fmt.Errorf(
			"this Gateway is configured for scope %q, but this enrollment command targets scope %q; uninstall the existing Gateway before changing its scope",
			state.GatewayScope,
			cfg.GatewayScope,
		)
	}
	hasNode := strings.TrimSpace(state.NodeID) != ""
	if hasNode && strings.TrimSpace(state.APIBase) == "" {
		return "", fmt.Errorf(
			"the existing Agent control-plane identity could not be verified; uninstall the existing Agent before installing it again",
		)
	}
	if state.APIBase != "" && !sameControlPlaneAddress(state.APIBase, cfg.APIBase) {
		return "", fmt.Errorf(
			"this Agent is configured for control plane %q, but this enrollment command targets %q; uninstall the existing Agent before installing it for another control plane",
			state.APIBase,
			cfg.APIBase,
		)
	}
	if state.WSSURL != "" && cfg.WSSURL != "" && !sameControlPlaneAddress(state.WSSURL, cfg.WSSURL) {
		return "", fmt.Errorf(
			"this Agent is configured for a different control-plane WebSocket endpoint; uninstall the existing Agent before installing it for another control plane",
		)
	}
	return "", nil
}

func gatewayScopeMismatch(installed, requested string) bool {
	installed = strings.TrimSpace(installed)
	requested = strings.TrimSpace(requested)
	return installed != "" && requested != "" && !strings.EqualFold(installed, requested)
}

func legacyLayoutRequiresMigration(state InstallState, mode InstallMode) bool {
	return state.Installed && state.LegacyLayout && mode == InstallModeAuto
}

// sameControlPlaneAddress compares stable endpoint identity while ignoring
// harmless trailing slashes and URL query/fragment values.
func sameControlPlaneAddress(installed, requested string) bool {
	installed = normalizeControlPlaneAddress(installed)
	requested = normalizeControlPlaneAddress(requested)
	return installed != "" && installed == requested
}

func normalizeControlPlaneAddress(raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return ""
	}
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return strings.TrimRight(strings.ToLower(raw), "/")
	}
	parsed.Scheme = strings.ToLower(parsed.Scheme)
	hostname := strings.ToLower(parsed.Hostname())
	port := parsed.Port()
	if (parsed.Scheme == "http" || parsed.Scheme == "ws") && port == "80" ||
		(parsed.Scheme == "https" || parsed.Scheme == "wss") && port == "443" {
		port = ""
	}
	parsed.Host = hostname
	if strings.Contains(hostname, ":") {
		parsed.Host = "[" + hostname + "]"
	}
	if port != "" {
		parsed.Host = net.JoinHostPort(hostname, port)
	}
	parsed.RawQuery = ""
	parsed.Fragment = ""
	parsed.Path = strings.TrimRight(parsed.Path, "/")
	parsed.RawPath = ""
	return strings.TrimRight(parsed.String(), "/")
}
