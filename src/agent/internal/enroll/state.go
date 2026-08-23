package enroll

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"runtime"
	"strings"

	"hyperfilelens/agent/internal/platform/install"
	"hyperfilelens/agent/internal/platform/vfs"
)

// InstallState describes an existing agent installation on this host.
type InstallState struct {
	Installed bool
	// LegacyLayout is true when the pre-unified machine-wide layout was found.
	// A legacy system installation must take the migration path even when its
	// version matches the current release, otherwise Gateway setup can read the
	// new agent.env path before the old identity has been copied.
	LegacyLayout     bool
	Version          string
	NodeID           string
	OrgKey           string
	Role             string
	InstallationMode string
	Service          string
}

// DetectInstallState inspects the default install paths for an existing agent.
func DetectInstallState() InstallState {
	installDir := install.DefaultInstallDir()
	bin := filepath.Join(installDir, agentBinaryName())
	legacy := false
	if info, err := os.Stat(bin); err != nil || info.IsDir() {
		if legacyBin := legacyAgentBinaryPath(); legacyBin != "" {
			if legacyInfo, legacyErr := os.Stat(legacyBin); legacyErr == nil && !legacyInfo.IsDir() {
				bin = legacyBin
				installDir = filepath.Dir(legacyBin)
				legacy = true
			}
		}
	}
	info, err := os.Stat(bin)
	if err != nil || info.IsDir() {
		return InstallState{}
	}

	state := InstallState{Installed: true, LegacyLayout: legacy}
	versionRoot := filepath.Dir(installDir)
	if legacy || filepath.Base(installDir) != "bin" {
		versionRoot = installDir
	}
	if data, err := os.ReadFile(vfs.AgentInstalledVersionPath(versionRoot)); err == nil {
		state.Version = strings.TrimSpace(string(data))
	}

	envPath := installedEnvPath()
	state.NodeID = ReadNodeID(envPath)
	state.OrgKey = readEnvKey(envPath, "HFL_ORG_KEY")
	state.Role = readEnvKey(envPath, "HFL_NODE_ROLE")
	state.InstallationMode = readEnvKey(envPath, "HFL_INSTALLATION_MODE")
	state.Service = serviceState(context.Background())
	return state
}

// legacyAgentBinaryPath returns the pre-unified machine-wide Agent binary.
// It is intentionally used only for upgrade discovery. New installations and
// all runtime paths continue to use the canonical Agent Root.
func legacyAgentBinaryPath() string {
	if runtime.GOOS != "linux" || vfs.UserInstallation() {
		return ""
	}
	return filepath.Join("/opt", vfs.UnixProductSlug, agentBinaryName())
}

// legacyAgentEnvPath returns the pre-unified Linux machine-wide state file.
func legacyAgentEnvPath() string {
	if runtime.GOOS != "linux" || vfs.UserInstallation() {
		return ""
	}
	return filepath.Join("/var/lib", vfs.UnixProductSlug, "agent.env")
}

// installedEnvPath prefers the canonical Agent Root, but falls back to the
// old machine-wide state file during the first cross-layout upgrade. Reading
// the old identity before generating a new one keeps the same console Node.
func installedEnvPath() string {
	canonical := EnvFilePath()
	legacy := legacyAgentEnvPath()
	if legacy == "" {
		return canonical
	}
	if _, err := os.Stat(legacy); err != nil {
		return canonical
	}
	if _, err := os.Stat(canonical); err != nil {
		return legacy
	}
	// A canonical env file can be staged before the old database is migrated.
	// In that partial state the old env remains authoritative, even if the
	// staged file already contains a newly generated identity.
	canonicalDB := filepath.Join(vfs.AgentDataStoreDir(dataDirForAgent()), "agent.db")
	if _, err := os.Stat(canonicalDB); err != nil {
		return legacy
	}
	return canonical
}

// ConflictingInstallPath returns the other lifecycle mode installed for the
// current host account. System installers also inspect the invoking sudo user.
func ConflictingInstallPath() string {
	var candidate string
	if vfs.UserInstallation() {
		candidate = vfs.SystemInstallDir()
		if !installMarkersPresent(candidate) {
			candidate = vfs.LegacySystemInstallDir()
		}
	} else {
		home := ""
		if sudoUser := strings.TrimSpace(os.Getenv("SUDO_USER")); sudoUser != "" {
			if account, err := user.Lookup(sudoUser); err == nil {
				home = account.HomeDir
			}
		}
		if home == "" {
			home, _ = os.UserHomeDir()
		}
		if home != "" {
			candidate = vfs.UserInstallDirForHome(home)
			if !installMarkersPresent(candidate) {
				candidate = vfs.LegacyUserInstallDirForHome(home)
			}
		}
	}
	if candidate == "" {
		return ""
	}
	if installMarkersPresent(candidate) {
		return candidate
	}
	return ""
}

func installMarkersPresent(installDir string) bool {
	paths := []string{}
	for _, name := range []string{
		agentBinaryName(),
		"install.sh",
		"install.ps1",
		"install.cmd",
	} {
		paths = append(paths, filepath.Join(installDir, name))
	}
	root := filepath.Dir(installDir)
	paths = append(paths, vfs.AgentManifestPath(root), vfs.AgentInstalledVersionPath(root))
	// Legacy pre-unified installations kept metadata beside the binaries.
	paths = append(paths, filepath.Join(installDir, "MANIFEST.json"), filepath.Join(installDir, "INSTALLED_VERSION"))
	for _, path := range paths {
		info, err := os.Stat(path)
		if err == nil && !info.IsDir() {
			return true
		}
	}
	return false
}

// ServiceHealthy reports whether the platform service appears to be running.
func (s InstallState) ServiceHealthy() bool {
	return isServiceHealthy(s.Service)
}

func isServiceHealthy(service string) bool {
	switch strings.ToLower(strings.TrimSpace(service)) {
	case "active", "running", "loaded":
		return true
	default:
		return false
	}
}

func readEnvKey(envPath, key string) string {
	data, err := os.ReadFile(envPath)
	if err != nil {
		return ""
	}
	prefix := key + "="
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, prefix) {
			return strings.TrimSpace(strings.TrimPrefix(line, prefix))
		}
	}
	return ""
}

func serviceState(ctx context.Context) string {
	switch runtime.GOOS {
	case "linux":
		if _, err := exec.LookPath("systemctl"); err != nil {
			return "unavailable"
		}
		args := []string{"is-active", "hyperfilelens-agent.service"}
		if vfs.UserInstallation() {
			args = append([]string{"--user"}, args...)
		}
		active, _ := exec.CommandContext(ctx, "systemctl", args...).Output()
		return strings.TrimSpace(string(active))
	case "darwin":
		domain := "system"
		if vfs.UserInstallation() {
			domain = fmt.Sprintf("gui/%d", os.Geteuid())
		}
		out, err := exec.CommandContext(ctx, "launchctl", "print", domain+"/com.hyperfilelens.agent").CombinedOutput()
		if err != nil {
			return "not loaded"
		}
		for _, line := range strings.Split(string(out), "\n") {
			line = strings.TrimSpace(line)
			if strings.HasPrefix(line, "state =") {
				return strings.Trim(strings.TrimPrefix(line, "state ="), " ;")
			}
		}
		return "loaded"
	case "windows":
		if vfs.UserInstallation() {
			out, err := exec.CommandContext(
				ctx,
				"powershell.exe",
				"-NoProfile",
				"-Command",
				"$task = Get-ScheduledTask -TaskName HyperFileLensAgent -ErrorAction SilentlyContinue; if ($null -eq $task) { exit 1 }; $task.State.ToString()",
			).CombinedOutput()
			if err != nil {
				return "not installed"
			}
			return strings.TrimSpace(string(out))
		}
		out, err := exec.CommandContext(ctx, "sc.exe", "query", "HyperFileLensAgent").CombinedOutput()
		if err != nil {
			return "not installed"
		}
		for _, line := range strings.Split(string(out), "\n") {
			line = strings.TrimSpace(line)
			if strings.HasPrefix(strings.ToUpper(line), "STATE") {
				fields := strings.Fields(line)
				if len(fields) >= 4 {
					return fields[3]
				}
			}
		}
		return "unknown"
	default:
		return "unknown"
	}
}

func dataDirForAgent() string {
	if configured := strings.TrimSpace(os.Getenv("HFL_DATA_DIR")); configured != "" {
		return filepath.Clean(configured)
	}
	return vfs.DefaultAgentDataDir()
}
