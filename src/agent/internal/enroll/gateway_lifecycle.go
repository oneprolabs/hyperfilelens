//go:build !windows

package enroll

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/vfs"
)

const gatewayLifecycleScript = "gateway-lifecycle.sh"

// RunGatewayUpgrade upgrades the HFL agent bundle (optional) and LensNode sidecar.
func RunGatewayUpgrade(ctx context.Context, fromArchive string) error {
	cfg, err := LoadConfigFromEnv()
	if err != nil {
		abortInstall("Initialization", err.Error(), 2, "HFL-UPGRADE-CONFIG")
	}
	if cfg.NodeRole != model.RoleGateway {
		abortInstall("Preflight checks", "gateway-upgrade requires HFL_NODE_ROLE=gateway", 2, "HFL-UPGRADE-ROLE")
	}
	if runtime.GOOS != "linux" {
		abortInstall("Preflight checks", "gateway-upgrade is Linux-only", 2, "HFL-UPGRADE-PLATFORM")
	}
	gatewayName := roleDisplayName(cfg.NodeRole, cfg.GatewayScope)
	currentVersion := "unknown"
	if version, versionErr := InstalledAgentVersion(ctx); versionErr == nil && version != "" {
		currentVersion = version
	}

	if !jsonOutput() {
		printLifecycleBanner(gatewayName, "Upgrade")
		fmt.Fprintln(os.Stdout)
		fmt.Fprintln(os.Stdout, "Target")
		printSummaryValue("Console", cfg.APIBase)
		printSummaryValue("Organization", cfg.OrgKey)
		printSummaryValue("Role", gatewayName)
		printSummaryValue("Current version", currentVersion)
		if strings.TrimSpace(fromArchive) != "" {
			printSummaryValue("Package source", fromArchive)
		}
	}
	printPhase("Preflight checks")
	logOK("Gateway role and platform support were verified.")
	commitInstallLog()

	fromArchive = strings.TrimSpace(fromArchive)
	if fromArchive != "" {
		printPhase("Upgrading Agent")
		if err := RunBundleUpgradeFromArchive(ctx, fromArchive, cfg); err != nil {
			return fmt.Errorf("agent upgrade: %w", err)
		}
	} else {
		logSkip("Agent package upgrade was not requested.")
	}

	printPhase("Upgrading AI engine")
	if err := runGatewayLifecycleScript(ctx, cfg, "upgrade-sidecar", false); err != nil {
		return err
	}
	logOK("AI engine upgrade completed.")

	printPhase("Verifying")
	service := serviceState(ctx)
	if service == "" {
		service = "active"
	}
	logOK("Agent service is " + service + ".")
	version := currentVersion
	if installedVersion, versionErr := InstalledAgentVersion(ctx); versionErr == nil && installedVersion != "" {
		version = installedVersion
	}
	printGatewayUpgradeSuccess(gatewayName, version, service)
	return nil
}

func printGatewayUpgradeSuccess(role, version, service string) {
	if jsonOutput() {
		emitJSON(os.Stdout, map[string]any{
			"type":            "upgrade_result",
			"result":          "success",
			"role":            role,
			"agent_version":   version,
			"agent_service":   service,
			"ai_engine_state": "active",
		})
		return
	}
	printResultRule(os.Stdout, "Upgrade completed successfully", ansiGreen)
	fmt.Fprintln(os.Stdout)
	fmt.Fprintln(os.Stdout, "Upgrade summary")
	printSummaryValue("Role", role)
	printSummaryValue("Agent version", version)
	printSummaryValue("Service state", service)
	printSummaryValue("AI engine", "active")
	printSummaryValue("Log file", activeInstallLogPath())
}

// RunGatewayUninstall removes LensNode sidecar then the HFL agent (default purge-all).
func RunGatewayUninstall(ctx context.Context, purgeAll bool) error {
	return runGatewayUninstall(ctx, purgeAll, true)
}

func runGatewayUninstall(ctx context.Context, purgeAll, renderLifecycle bool) error {
	cfg, err := LoadConfigFromEnv()
	if err != nil {
		abortInstall("Initialization", err.Error(), 2, "HFL-UNINSTALL-CONFIG")
	}
	if cfg.NodeRole != model.RoleGateway {
		abortInstall("Preflight checks", "gateway-uninstall requires HFL_NODE_ROLE=gateway", 2, "HFL-UNINSTALL-ROLE")
	}
	if runtime.GOOS != "linux" {
		abortInstall("Preflight checks", "gateway-uninstall is Linux-only", 2, "HFL-UNINSTALL-PLATFORM")
	}
	gatewayName := roleDisplayName(cfg.NodeRole, cfg.GatewayScope)
	state := DetectInstallState()
	if renderLifecycle {
		printUninstallContext(cfg.APIBase, cfg.OrgKey, cfg.NodeRole, state, purgeAll)
		printPhase("Preflight checks")
		logOK("Gateway role and platform support were verified.")
		commitInstallLog()
		printPhase("Uninstalling")
	}

	logStep("Removing AI engine.")
	if err := runGatewayLifecycleScript(ctx, cfg, "uninstall-sidecar", purgeAll); err != nil {
		return err
	}

	installDir := vfs.DefaultInstallDir()
	installScript := filepath.Join(installDir, "install.sh")
	if _, err := os.Stat(installScript); err != nil {
		logOK("Agent install bundle not found; AI engine removal completed.")
		if renderLifecycle {
			printPhase("Verifying")
			logOK("Managed AI engine resources were removed.")
			printUninstallSuccess(state, purgeAll)
		}
		return nil
	}

	logStep("Removing HyperFileLens agent.")
	args := []string{installScript, "uninstall", "--quiet-footer"}
	if purgeAll {
		args = append(args, "--purge-all")
	}
	cmd := exec.CommandContext(ctx, "/bin/bash", args...)
	cmd.Env = append(os.Environ(), "HFL_SKIP_GATEWAY_SIDECAR_UNINSTALL=1")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("agent uninstall: %w", err)
	}
	logOK(gatewayName + " uninstall completed.")
	if renderLifecycle {
		printPhase("Verifying")
		logOK("Agent service, installed files, and AI engine resources were removed.")
		printUninstallSuccess(state, purgeAll)
	}
	return nil
}

func runGatewayLifecycleScript(ctx context.Context, cfg Config, command string, purgeAll bool) error {
	scriptPath, cleanup, err := downloadGatewayBootstrapScript(ctx, cfg, gatewayLifecycleScript)
	if err != nil {
		return err
	}
	defer cleanup()

	args := []string{scriptPath, command}
	if purgeAll {
		args = append(args, "--purge-all")
	}
	cmd := exec.CommandContext(ctx, "/bin/bash", args...)
	cmd.Env = append(os.Environ(),
		"HFL_AGENT_ENV_FILE="+EnvFilePath(),
		"HFL_INSECURE_TLS="+insecureTLSEnv(),
	)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("%s: %w", command, err)
	}
	return nil
}
