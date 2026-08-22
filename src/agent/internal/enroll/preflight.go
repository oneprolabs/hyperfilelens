package enroll

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"strconv"
	"strings"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/hostinfo"
	"hyperfilelens/agent/internal/platform/release"
)

// EnvironmentReport is the result of pre-install environment checks.
type EnvironmentReport struct {
	Platform       string
	PrivilegesOK   bool
	ArchOK         bool
	ServiceMgr     string
	Existing       InstallState
	RoleOK         bool
	RoleError      string
	InstallationID string
}

// RunEnvironmentChecks validates the host and prints user-facing results.
func RunEnvironmentChecks(ctx context.Context, cfg Config) (*EnvironmentReport, error) {
	failures := &preflightFailures{}
	// Existing-install and cross-mode conflicts are deliberately determined
	// only from local installation markers. Do not make the control plane or a
	// host fingerprint an installation-admission authority.
	report := &EnvironmentReport{
		Platform: platformDescription(),
		ArchOK:   supportedRuntimeArch(),
		Existing: DetectInstallState(),
	}
	conflictingInstallPath := ConflictingInstallPath()

	if err := privilegeConstraint(cfg.InstallationMode); err != nil {
		report.PrivilegesOK = false
	} else {
		report.PrivilegesOK = true
	}

	report.ServiceMgr = detectLifecycleManager(ctx, cfg.InstallationMode)
	persistedInstallationID := readEnvKey(EnvFilePath(), "HFL_INSTALLATION_ID")
	installationID, identityErr := installationID(ctx, cfg)
	if identityErr == nil {
		report.InstallationID = installationID
	}
	hostname := checkHostname()
	printEnrollmentContext(cfg.APIBase, cfg.OrgKey, cfg.NodeRole, report.Platform, hostname.Name)

	consoleReach := checkConsoleReachable(ctx, cfg.APIBase)
	wssReach := checkWSSReachable(ctx, resolveWSSURL(cfg))
	clock := checkClockSync(ctx, cfg.APIBase)

	if err := roleConstraints(cfg.NodeRole); err != nil {
		report.RoleOK = false
		report.RoleError = err.Error()
	} else if err := lifecycleManagerConstraint(
		report.ServiceMgr,
		cfg.InstallationMode,
	); err != nil {
		report.RoleOK = false
		report.RoleError = err.Error()
	} else if err := userSessionLifecycleConstraint(ctx, cfg.InstallationMode); err != nil {
		report.RoleOK = false
		report.RoleError = err.Error()
	} else {
		report.RoleOK = true
	}

	if report.PrivilegesOK {
		if cfg.InstallationMode == model.InstallationModeUser {
			logOKDetail("Running as the current user", currentUserPrivilegeDetail())
		} else {
			logOKDetail("Running with administrator privileges", adminPrivilegeDetail())
		}
	} else {
		if cfg.InstallationMode == model.InstallationModeUser {
			failures.add(
				"User-level installation must not be elevated",
				"re-run the command as the current user without sudo or UAC elevation",
				1,
			)
		} else {
			failures.add("Administrator privileges are required", "re-run with sudo or as Administrator", 1)
		}
	}
	if conflictingInstallPath != "" {
		failures.add(
			"A different Agent installation mode already exists",
			conflictingInstallPath+"; uninstall it before changing installation mode",
			1,
		)
	}

	if report.ArchOK {
		logOKDetail("CPU architecture is supported", runtime.GOARCH)
	} else {
		failures.add("CPU architecture is not supported", runtime.GOARCH+" ("+supportedArchDescription()+")", 4)
	}

	if report.RoleOK {
		logOKDetail("Role is supported on this platform", fmt.Sprintf("%s on %s", cfg.NodeRole, runtime.GOOS))
	} else {
		failures.add("Role is not supported on this platform", report.RoleError, 1)
	}

	switch report.ServiceMgr {
	case "systemd", "launchd", "windows-service", "systemd-user", "launch-agent", "windows-task":
		logOKDetail("Service manager is available", report.ServiceMgr)
	case "none":
		logWarnDetail("No service manager was detected", "the agent can install but auto-start may be unavailable")
	default:
		logOKDetail("Service manager is available", report.ServiceMgr)
	}

	logHostnameResult(hostname)
	if identityErr != nil {
		failures.add(
			"Installation identity cannot be prepared",
			identityErr.Error(),
			2,
		)
	} else if report.Existing.Installed && persistedInstallationID != "" {
		logOKDetail("Installation identity is ready", "the existing installation identity will be reused")
	} else if report.Existing.Installed {
		logOKDetail("Installation identity is ready", "a new identity will be attached to the existing installation")
	} else {
		logOKDetail("Installation identity is ready", "a new identity will be persisted when installation begins")
	}
	logClockResult(clock)
	logReachResult(consoleReach, failures)
	logReachResult(wssReach, failures)
	logHostResources(checkHostResources(cfg.NodeRole), failures)

	if err := checkRequiredCommands(); err != nil {
		failures.add("Required commands are missing", err.Error(), 2)
	} else {
		logOKDetail("Required commands are available", requiredCommandsDetail())
	}

	logWritableResult(checkInstallPathsWritable(), failures)
	requiredSpace := uint64(defaultEnrollmentRequiredBytes)
	var gatewayCheck gatewayRuntimePreflightResult
	if consoleReach.OK {
		artifact, artifactErr := release.FetchArtifact(ctx, cfg.AgentConfig())
		if artifactErr != nil {
			if report.Existing.Installed {
				logWarnDetail(
					"Agent package metadata is unavailable",
					artifactErr.Error()+"; the requested existing-install action will validate authorization before making changes",
				)
			} else {
				failures.add("Agent package metadata is unavailable", artifactErr.Error(), 3)
			}
		} else {
			if artifact.RequiredSpace > requiredSpace {
				requiredSpace = artifact.RequiredSpace
			}
			logOKDetail(
				"Agent package metadata is available",
				fmt.Sprintf("version %s, download %s", artifact.Version, humanBytes(artifact.DownloadSize)),
			)
		}
	}
	if cfg.NodeRole == model.RoleGateway {
		gatewayCheck = checkGatewayRuntimePreflight(ctx, cfg)
		requiredSpace += gatewayCheck.RequiredSpace
		if lockDetail := packageManagerLockDetail(); lockDetail != "" &&
			!gatewayCheck.ExistingDocker && gatewayCheck.Err == nil {
			gatewayCheck.Err = fmt.Errorf("package manager is busy: %s", lockDetail)
		}
	}
	logDiskResult(checkEnrollmentDiskSpace(requiredSpace), failures)
	if cfg.NodeRole == model.RoleGateway {
		if gatewayCheck.Err != nil {
			failures.add(
				roleDisplayName(cfg.NodeRole, cfg.GatewayScope)+" runtime requirements are not met",
				gatewayCheck.Err.Error(),
				3,
			)
		} else {
			logOKDetail(
				roleDisplayName(cfg.NodeRole, cfg.GatewayScope)+" runtime plan is ready",
				gatewayCheck.Detail,
			)
		}
		for _, warning := range gatewayCheck.Warnings {
			logWarnDetail(roleDisplayName(cfg.NodeRole, cfg.GatewayScope)+" runtime needs attention", warning)
		}
	}

	nasOK, nasWarn, nasTitle, nasDetail := checkNASMountHelpers(string(cfg.NodeRole))
	switch {
	case nasOK && nasTitle != "":
		logOKDetail(nasTitle, nasDetail)
	case nasWarn:
		logWarnDetail(nasTitle, nasDetail)
	}

	if report.Existing.Installed {
		title := "An existing agent installation was detected"
		detail := strings.TrimPrefix(formatExistingInstallDetail(report.Existing), " (")
		detail = strings.TrimSuffix(detail, ")")
		if report.Existing.ServiceHealthy() {
			logOKDetail(title, detail)
		} else {
			logWarnDetail(title, detail)
		}
	} else {
		logOKDetail("No existing agent installation was found", defaultInstallPath())
	}

	return report, failures.err()
}

func adminPrivilegeDetail() string {
	if runtime.GOOS == "windows" {
		return "elevated Administrator session"
	}
	return "root"
}

func currentUserPrivilegeDetail() string {
	if user, err := os.UserHomeDir(); err == nil && user != "" {
		return user
	}
	return "non-elevated session"
}

func formatExistingInstallDetail(state InstallState) string {
	parts := []string{}
	if state.NodeID != "" {
		parts = append(parts, "node "+state.NodeID)
	}
	if state.Version != "" {
		parts = append(parts, "v"+state.Version)
	}
	if state.Service != "" && state.Service != "unknown" {
		parts = append(parts, "service "+state.Service)
	}
	if len(parts) == 0 {
		return ""
	}
	return " (" + strings.Join(parts, ", ") + ")"
}

// Preflight checks privileges and role/OS constraints (legacy entry point).
func Preflight(role model.Role) error {
	if err := requireAdmin(); err != nil {
		return err
	}
	if !supportedRuntimeArch() {
		return fmt.Errorf("unsupported arch %s (%s)", runtime.GOARCH, supportedArchDescription())
	}
	if err := roleConstraints(role); err != nil {
		return err
	}
	return serviceManagerConstraint(detectServiceManager(context.Background()))
}

func supportedRuntimeArch() bool {
	switch runtime.GOOS {
	case "linux", "darwin":
		return runtime.GOARCH == "amd64" || runtime.GOARCH == "arm64"
	case "windows":
		return runtime.GOARCH == "amd64"
	default:
		return false
	}
}

func supportedArchDescription() string {
	if runtime.GOOS == "windows" {
		return "only amd64"
	}
	return "only amd64/arm64"
}

func roleConstraints(role model.Role) error {
	if role == model.RoleProxy || role == model.RoleGateway {
		if runtime.GOOS != "linux" {
			return fmt.Errorf("role %s is Linux-only", role)
		}
		if runtime.GOARCH != "amd64" {
			return fmt.Errorf("role %s requires linux/amd64", role)
		}
		if !isSupportedGatewayUbuntu() {
			return fmt.Errorf("role %s requires Ubuntu 20.04, 22.04, or 24.04 LTS", role)
		}
	}
	return nil
}

func isSupportedGatewayUbuntu() bool {
	f, err := os.Open("/etc/os-release")
	if err != nil {
		return false
	}
	defer f.Close()
	id := ""
	versionID := ""
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		if strings.HasPrefix(line, "ID=") {
			id = strings.Trim(strings.TrimPrefix(line, "ID="), `"`)
		}
		if strings.HasPrefix(line, "VERSION_ID=") {
			versionID = strings.Trim(strings.TrimPrefix(line, "VERSION_ID="), `"`)
		}
	}
	return id == "ubuntu" && (versionID == "20.04" || versionID == "22.04" || versionID == "24.04")
}

func detectServiceManager(ctx context.Context) string {
	return hostinfo.DetectServiceManager(ctx)
}

func serviceManagerConstraint(manager string) error {
	return lifecycleManagerConstraint(manager, model.InstallationModeSystem)
}

func lifecycleManagerConstraint(
	manager string,
	mode model.InstallationMode,
) error {
	if mode == model.InstallationModeUser {
		switch runtime.GOOS {
		case "linux":
			if manager != "systemd-user" {
				return fmt.Errorf("a working systemd user service manager is required for user-level installation")
			}
		case "darwin":
			if manager != "launch-agent" {
				return fmt.Errorf("a launchd user session is required for user-level installation on macOS")
			}
		case "windows":
			if manager != "windows-task" {
				return fmt.Errorf("Windows Task Scheduler is required for user-level installation")
			}
		default:
			return fmt.Errorf("operating system %s is not supported", runtime.GOOS)
		}
		return nil
	}
	switch runtime.GOOS {
	case "linux":
		if manager != "systemd" {
			return fmt.Errorf("this release requires a systemd-based Linux distribution; OpenRC, non-systemd, and container deployments are not supported")
		}
	case "darwin":
		if manager != "launchd" {
			return fmt.Errorf("launchd is required to install the agent service on macOS")
		}
	case "windows":
		if manager != "windows-service" {
			return fmt.Errorf("Windows Service Manager is required to install the agent service")
		}
	default:
		return fmt.Errorf("operating system %s is not supported", runtime.GOOS)
	}
	return nil
}

func detectLifecycleManager(
	ctx context.Context,
	mode model.InstallationMode,
) string {
	if mode != model.InstallationModeUser {
		return detectServiceManager(ctx)
	}
	switch runtime.GOOS {
	case "linux":
		if _, err := exec.LookPath("systemctl"); err != nil {
			return "none"
		}
		if exec.CommandContext(ctx, "systemctl", "--user", "show-environment").Run() == nil {
			return "systemd-user"
		}
		return "none"
	case "darwin":
		if _, err := exec.LookPath("launchctl"); err == nil &&
			exec.CommandContext(
				ctx,
				"launchctl",
				"print",
				fmt.Sprintf("gui/%d", os.Geteuid()),
			).Run() == nil {
			return "launch-agent"
		}
		return "none"
	case "windows":
		if _, err := exec.LookPath("schtasks.exe"); err == nil {
			return "windows-task"
		}
		return "none"
	default:
		return "unknown"
	}
}

func privilegeConstraint(mode model.InstallationMode) error {
	if mode != model.InstallationModeUser {
		return requireAdmin()
	}
	if runtime.GOOS == "windows" {
		if requireWindowsAdmin() == nil {
			return fmt.Errorf("user-level installation must run without UAC elevation")
		}
		return nil
	}
	if os.Geteuid() == 0 {
		return fmt.Errorf("user-level installation must run without sudo")
	}
	return nil
}

func userSessionLifecycleConstraint(
	ctx context.Context,
	mode model.InstallationMode,
) error {
	if mode != model.InstallationModeUser || runtime.GOOS != "linux" {
		return nil
	}
	if _, err := exec.LookPath("loginctl"); err != nil {
		return fmt.Errorf(
			"loginctl is required to verify that current-user mode stops after sign-out",
		)
	}
	out, err := exec.CommandContext(
		ctx,
		"loginctl",
		"show-user",
		strconv.Itoa(os.Geteuid()),
		"--property=Linger",
		"--value",
	).Output()
	if err != nil {
		return fmt.Errorf("unable to verify the current user's systemd sign-out behavior")
	}
	if strings.EqualFold(strings.TrimSpace(string(out)), "yes") {
		return fmt.Errorf(
			"current-user mode must pause after sign-out, but systemd user lingering is enabled; disable lingering or choose system mode",
		)
	}
	return nil
}

func requireAdmin() error {
	if runtime.GOOS == "windows" {
		return requireWindowsAdmin()
	}
	if os.Geteuid() != 0 {
		return fmt.Errorf("re-run with sudo or as Administrator to install the agent service and %s", defaultInstallPath())
	}
	return nil
}

func isUbuntuMin(major, minor int) bool {
	f, err := os.Open("/etc/os-release")
	if err != nil {
		return false
	}
	defer f.Close()
	id := ""
	versionID := ""
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		if strings.HasPrefix(line, "ID=") {
			id = strings.Trim(strings.TrimPrefix(line, "ID="), `"`)
		}
		if strings.HasPrefix(line, "VERSION_ID=") {
			versionID = strings.Trim(strings.TrimPrefix(line, "VERSION_ID="), `"`)
		}
	}
	if id != "ubuntu" {
		return false
	}
	return ubuntuVersionAtLeast(versionID, major, minor)
}

func ubuntuVersionAtLeast(versionID string, major, minor int) bool {
	parts := strings.SplitN(versionID, ".", 2)
	if len(parts) < 2 {
		return false
	}
	maj, err1 := strconv.Atoi(parts[0])
	min, err2 := strconv.Atoi(parts[1])
	if err1 != nil || err2 != nil {
		return false
	}
	if maj > major {
		return true
	}
	if maj < major {
		return false
	}
	return min >= minor
}

func requireWindowsAdmin() error {
	cmd := exec.Command("powershell", "-NoProfile", "-Command",
		"([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)")
	out, err := cmd.Output()
	if err != nil {
		return fmt.Errorf("Administrator privileges required")
	}
	if !strings.Contains(strings.ToLower(string(out)), "true") {
		return fmt.Errorf("Administrator privileges required")
	}
	return nil
}
