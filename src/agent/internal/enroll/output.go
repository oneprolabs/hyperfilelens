package enroll

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"

	"github.com/mattn/go-isatty"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/vfs"
)

const (
	ansiReset   = "\033[0m"
	ansiBold    = "\033[1m"
	ansiGreen   = "\033[32m"
	ansiYellow  = "\033[33m"
	ansiRed     = "\033[31m"
	ansiCyan    = "\033[36m"
	ansiMagenta = "\033[35m"
)

var useColor bool
var bannerPrinted bool

// InstallFailure is a typed installer failure rendered at the command boundary.
type InstallFailure struct {
	Stage   string
	Reason  string
	Code    int
	CodeKey string
}

func (failure InstallFailure) Error() string { return failure.Reason }

func initOutput() {
	mode := strings.ToLower(strings.TrimSpace(os.Getenv("HFL_OUTPUT")))
	if mode == "plain" || mode == "json" || os.Getenv("NO_COLOR") != "" ||
		os.Getenv("HFL_ENROLL_NO_COLOR") != "" {
		useColor = false
		return
	}
	stdout := commandStdout()
	useColor = isatty.IsTerminal(stdout.Fd()) || isatty.IsCygwinTerminal(stdout.Fd())
}

func colorize(code, value string) string {
	if !useColor || code == "" {
		return value
	}
	return code + value + ansiReset
}

func emitLine(level, message string, writer io.Writer) {
	initOutput()
	message = strings.TrimSpace(message)
	if message == "" {
		return
	}
	if jsonOutput() {
		emitJSON(writer, map[string]any{
			"type":    "install_event",
			"status":  strings.TrimSpace(level),
			"message": message,
		})
		return
	}
	styled := level
	switch level {
	case " OK ":
		styled = colorize(ansiGreen, level)
	case "WARN":
		styled = colorize(ansiYellow, level)
	case "FAIL":
		styled = colorize(ansiRed, level)
	case "SKIP":
		styled = colorize(ansiCyan, level)
	case "....":
		styled = colorize(ansiMagenta, level)
	case "INFO":
		styled = colorize(ansiCyan, level)
	}
	_, _ = fmt.Fprintf(writer, "  [%s] %s\n", styled, message)
}

func emitDetailLine(level, title, detail string, writer io.Writer) {
	title = strings.TrimSpace(title)
	detail = strings.TrimSpace(detail)
	if detail == "" || jsonOutput() {
		emitLine(level, joinDetail(title, detail), writer)
		return
	}
	inline := title + " · " + detail
	if detailFitsTerminal(level, inline, writer) {
		emitLine(level, inline, writer)
		return
	}
	emitLine(level, title, writer)
	_, _ = fmt.Fprintf(writer, "         %s\n", detail)
}

func detailFitsTerminal(level, message string, writer io.Writer) bool {
	columns := terminalColumns(writer)
	if columns <= 0 {
		// Pipes and redirected output should not acquire artificial line breaks.
		// The persisted log remains complete and the receiving terminal can wrap
		// naturally when it renders the stream.
		return true
	}
	// emitLine prefixes the message with two spaces, a five-character status
	// token in brackets, and one separator space.
	prefixWidth := 2 + len("[") + len(level) + len("] ")
	return prefixWidth+displayWidth(message) <= columns
}

func displayWidth(value string) int {
	return len([]rune(value))
}

func logInfo(message string) { emitLine("INFO", message, os.Stdout) }
func logOK(message string)   { emitLine(" OK ", message, os.Stdout) }
func logSkip(message string) { emitLine("SKIP", message, os.Stdout) }
func logWarn(message string) { emitLine("WARN", message, os.Stderr) }
func logStep(message string) { emitLine("....", message, os.Stdout) }

func logFail(message string, code int) {
	abortInstall("Installing", message, code, fmt.Sprintf("HFL-INSTALL-%03d", code))
}

func abortInstall(stage, message string, code int, codeKey string) {
	message = strings.TrimSpace(message)
	emitLine("FAIL", message, os.Stderr)
	panic(InstallFailure{
		Stage:   stage,
		Reason:  message,
		Code:    code,
		CodeKey: codeKey,
	})
}

// RecoverInstallFailure converts the internal abort boundary into a normal error.
func RecoverInstallFailure(target *error) {
	if recovered := recover(); recovered != nil {
		if failure, ok := recovered.(InstallFailure); ok {
			*target = failure
			return
		}
		panic(recovered)
	}
}

// PrintCommandFailure renders a stable final failure block for returned errors.
func PrintCommandFailure(err error) {
	PrintCommandFailureFor("install", err)
}

// PrintCommandFailureFor renders a stable failure block for one lifecycle operation.
func PrintCommandFailureFor(operation string, err error) {
	if err == nil {
		return
	}
	operation = normalizeFailureOperation(operation)
	noun, codePrefix, activeStage := failureOperationLabels(operation)
	failure := InstallFailure{
		Stage:   activeStage,
		Reason:  err.Error(),
		Code:    1,
		CodeKey: codePrefix + "-001",
	}
	var typed InstallFailure
	if errors.As(err, &typed) {
		failure = typed
	} else if errors.Is(err, ErrInstallLocked) {
		failure.Stage = "Initialization"
		failure.Reason = "Another HyperFileLens installation is already running."
		failure.CodeKey = codePrefix + "-LOCKED"
	}
	if jsonOutput() {
		emitJSON(os.Stderr, map[string]any{
			"type":          "install_result",
			"operation":     operation,
			"result":        "failed",
			"stage":         failure.Stage,
			"reason":        ensureSentence(failure.Reason),
			"error_code":    failure.CodeKey,
			"system_change": failure.Stage != "Preflight checks" && failure.Stage != "Initialization",
		})
		return
	}
	title := noun + " failed"
	systemChange := "See the cleanup status above"
	if failure.Stage == "Preflight checks" || failure.Stage == "Initialization" {
		title = noun + " was not started"
		systemChange = "None"
	}
	if operation == "install" && failure.Stage == "Post-install verification" {
		title = "Installation completed, but verification failed"
		systemChange = "Agent installed; verification requires attention"
	}
	printResultRule(os.Stderr, title, ansiRed)
	fmt.Fprintln(os.Stderr)
	fmt.Fprintln(os.Stderr, "Failure")
	fmt.Fprintf(os.Stderr, "  %-13s %s\n", "Stage", failure.Stage)
	fmt.Fprintf(os.Stderr, "  %-13s %s\n", "Reason", ensureSentence(failure.Reason))
	fmt.Fprintf(os.Stderr, "  %-13s %s\n", "System change", systemChange)
	fmt.Fprintln(os.Stderr)
	fmt.Fprintln(os.Stderr, "Error code:")
	fmt.Fprintf(os.Stderr, "  %s\n", failure.CodeKey)
	if operation == "install" && failure.Stage == "Post-install verification" {
		fmt.Fprintln(os.Stderr)
		fmt.Fprintln(os.Stderr, "Suggested actions:")
		fmt.Fprintln(os.Stderr, "  1. Check outbound network access to the control plane.")
		fmt.Fprintln(os.Stderr, "  2. Confirm that any proxy supports WebSocket connections.")
		fmt.Fprintln(os.Stderr, "  3. Review the Agent service log and run hfl-enroll status.")
	}
}

func normalizeFailureOperation(operation string) string {
	switch strings.ToLower(strings.TrimSpace(operation)) {
	case "upgrade", "uninstall", "registration", "status":
		return strings.ToLower(strings.TrimSpace(operation))
	default:
		return "install"
	}
}

func failureOperationLabels(operation string) (noun, codePrefix, activeStage string) {
	switch operation {
	case "upgrade":
		return "Upgrade", "HFL-UPGRADE", "Upgrading Agent"
	case "uninstall":
		return "Uninstallation", "HFL-UNINSTALL", "Uninstalling"
	case "registration":
		return "Registration", "HFL-REGISTER", "Registering Agent"
	case "status":
		return "Status check", "HFL-STATUS", "Checking status"
	default:
		return "Installation", "HFL-INSTALL", "Initialization"
	}
}

func ensureSentence(message string) string {
	message = strings.TrimSpace(message)
	if message == "" {
		return message
	}
	switch message[len(message)-1] {
	case '.', '?', '!':
		return message
	default:
		return message + "."
	}
}

func printBanner(role string) {
	printLifecycleBanner(role, "Installer")
}

func printLifecycleBanner(role, operation string) {
	if bannerPrinted || os.Getenv("HFL_NO_BANNER") != "" {
		return
	}
	bannerPrinted = true
	columns, _ := strconv.Atoi(strings.TrimSpace(os.Getenv("COLUMNS")))
	if columns > 0 && columns < 96 {
		fmt.Fprintln(os.Stdout, colorize(ansiBold+ansiMagenta, "HyperFileLens Installer"))
	} else {
		fmt.Fprintln(os.Stdout, colorize(ansiBold+ansiMagenta, ` _   _                       _____ _ _      _
| | | |_   _ _ __   ___ _ _|  ___(_) | ___| |    ___ _ __  ___
| |_| | | | | '_ \ / _ \ '__| |_  | | |/ _ \ |   / _ \ '_ \/ __|
|  _  | |_| | |_) |  __/ |  |  _| | | |  __/ |__|  __/ | | \__ \
|_| |_|\__, | .__/ \___|_|  |_|   |_|_|\___|_____\___|_| |_|___/
       |___/|_|                     INSTALLER`))
	}
	fmt.Fprintf(os.Stdout, "\nHyperFileLens %s %s\n", role, operation)
	fmt.Fprintln(os.Stdout, strings.Repeat("-", 64))
}

func printPhase(title string) {
	title = strings.TrimSpace(title)
	if title == "" {
		return
	}
	if jsonOutput() {
		emitJSON(os.Stdout, map[string]any{
			"type":    "install_phase",
			"status":  "STEP",
			"message": title,
		})
		return
	}
	fmt.Fprintln(os.Stdout)
	fmt.Fprintln(os.Stdout, title)
}

// SummaryInfo is the final enrollment summary block.
type SummaryInfo struct {
	Role        string
	NodeID      string
	Version     string
	Service     string
	LensNode    string
	Console     string
	InstallPath string
	DataPath    string
	LogPath     string
}

func printEnrollmentContext(
	consoleURL string,
	orgKey string,
	role model.Role,
	platform string,
	hostname string,
) {
	displayRole := roleDisplayName(role, os.Getenv("HFL_GATEWAY_SCOPE"))
	if jsonOutput() {
		emitJSON(os.Stdout, map[string]any{
			"type":         "install_target",
			"console":      consoleURL,
			"organization": orgKey,
			"role":         displayRole,
			"hostname":     hostname,
			"platform":     platform,
		})
		return
	}
	if parentSession() {
		var detail strings.Builder
		fmt.Fprintln(&detail, "Target")
		printSummaryValueTo(&detail, "Console", consoleURL)
		printSummaryValueTo(&detail, "Organization", orgKey)
		printSummaryValueTo(&detail, "Role", displayRole)
		printSummaryValueTo(&detail, "Hostname", hostname)
		printSummaryValueTo(&detail, "Platform", platform)
		fmt.Fprintln(&detail)
		fmt.Fprintln(&detail, "Preflight checks")
		writeCommandLogOnly(detail.String())
		printPhase("Platform Data Gateway preflight checks")
		return
	}
	printBanner(displayRole)
	fmt.Fprintln(os.Stdout)
	fmt.Fprintln(os.Stdout, "Target")
	printSummaryValue("Console", consoleURL)
	printSummaryValue("Organization", orgKey)
	printSummaryValue("Role", displayRole)
	printSummaryValue("Hostname", hostname)
	printSummaryValue("Platform", platform)
	fmt.Fprintln(os.Stdout)
	fmt.Fprintln(os.Stdout, "Preflight checks")
}

func printUninstallContext(
	consoleURL string,
	orgKey string,
	role model.Role,
	state InstallState,
	purgeAll bool,
) {
	displayRole := roleDisplayName(role, os.Getenv("HFL_GATEWAY_SCOPE"))
	dataPolicy := "Preserve Agent data"
	if purgeAll {
		dataPolicy = "Remove Agent data"
	}
	if jsonOutput() {
		emitJSON(os.Stdout, map[string]any{
			"type":         "uninstall_target",
			"console":      consoleURL,
			"organization": orgKey,
			"role":         displayRole,
			"node_id":      state.NodeID,
			"version":      state.Version,
			"service":      state.Service,
			"data_policy":  dataPolicy,
		})
		return
	}
	printLifecycleBanner(displayRole, "Uninstaller")
	fmt.Fprintln(os.Stdout)
	fmt.Fprintln(os.Stdout, "Target")
	printSummaryValue("Console", consoleURL)
	printSummaryValue("Organization", orgKey)
	printSummaryValue("Role", displayRole)
	printSummaryValue("Node ID", state.NodeID)
	printSummaryValue("Agent version", state.Version)
	printSummaryValue("Service state", state.Service)
	printSummaryValue("Install path", defaultInstallPath())
	printSummaryValue("Data path", dataDirForAgent())
	printSummaryValue("Data removal", dataPolicy)
}

func printUninstallSuccess(state InstallState, purgeAll bool) {
	dataState := "preserved"
	logState := filepath.Join(vfs.AgentLogDir(dataDirForAgent()), "uninstall.log")
	if purgeAll {
		dataState = "removed"
		logState = "removed with Agent data"
	}
	if jsonOutput() {
		emitJSON(os.Stdout, map[string]any{
			"type":            "uninstall_result",
			"result":          "success",
			"node_id":         state.NodeID,
			"install_path":    "removed",
			"data_path_state": dataState,
			"log_file":        logState,
		})
		return
	}
	printResultRule(os.Stdout, "Uninstallation completed successfully", ansiGreen)
	fmt.Fprintln(os.Stdout)
	fmt.Fprintln(os.Stdout, "Uninstallation summary")
	printSummaryValue("Node ID", state.NodeID)
	printSummaryValue("Service", "removed")
	printSummaryValue("Install path", "removed")
	printSummaryValue("Data path", dataState)
	printSummaryValue("Console record", "not changed by local uninstall")
	printSummaryValue("Log file", logState)
}

func summaryFromState(consoleURL, nodeID, version, service string) SummaryInfo {
	return SummaryInfo{
		NodeID:      nodeID,
		Version:     version,
		Service:     service,
		InstallPath: defaultInstallPath(),
		DataPath:    dataDirForAgent(),
		Console:     consoleURL,
		LogPath:     activeInstallLogPath(),
	}
}

func printEnrollmentSuccess(info SummaryInfo) {
	if info.Role == "" {
		info.Role = roleDisplayName(model.RoleAgent)
	}
	if jsonOutput() {
		emitJSON(os.Stdout, map[string]any{
			"type":          "install_result",
			"result":        "success",
			"role":          info.Role,
			"node_id":       info.NodeID,
			"agent_version": info.Version,
			"agent_service": info.Service,
			"lensnode":      info.LensNode,
			"console_state": "online",
			"install_path":  info.InstallPath,
			"data_path":     info.DataPath,
			"config_path":   filepath.Join(info.DataPath, "config"),
			"log_file":      info.LogPath,
		})
		return
	}
	if parentSession() {
		var detail strings.Builder
		fmt.Fprintln(&detail, strings.Repeat("=", 64))
		fmt.Fprintln(&detail, "Installation completed successfully")
		fmt.Fprintln(&detail, strings.Repeat("=", 64))
		fmt.Fprintln(&detail)
		fmt.Fprintln(&detail, "Installation summary")
		printSummaryValueTo(&detail, "Role", info.Role)
		printSummaryValueTo(&detail, "Node ID", info.NodeID)
		printSummaryValueTo(&detail, "Agent version", info.Version)
		printSummaryValueTo(&detail, "Service state", info.Service)
		printSummaryValueTo(&detail, "AI engine", info.LensNode)
		printSummaryValueTo(&detail, "Console state", "online")
		printSummaryValueTo(&detail, "Agent root", info.DataPath)
		printSummaryValueTo(&detail, "Binaries", info.InstallPath)
		printSummaryValueTo(&detail, "Config", filepath.Join(info.DataPath, "config"))
		printSummaryValueTo(&detail, "Log file", info.LogPath)
		fmt.Fprintln(&detail)
		fmt.Fprintln(&detail, "Next step")
		fmt.Fprintln(&detail, "  Open HyperFileLens and configure the data sources available")
		fmt.Fprintln(&detail, "  through this Gateway.")
		fmt.Fprintln(&detail)
		writeAgentLifecycleCommands(&detail, info)
		writeCommandLogOnly(detail.String())
		return
	}
	printResultRule(os.Stdout, "Installation completed successfully", ansiGreen)
	fmt.Fprintln(os.Stdout)
	fmt.Fprintln(os.Stdout, "Installation summary")
	printSummaryValue("Role", info.Role)
	printSummaryValue("Node ID", info.NodeID)
	printSummaryValue("Agent version", info.Version)
	printSummaryValue("Service state", info.Service)
	printSummaryValue("AI engine", info.LensNode)
	printSummaryValue("Console state", "online")
	printSummaryValue("Agent root", info.DataPath)
	printSummaryValue("Binaries", info.InstallPath)
	printSummaryValue("Config", filepath.Join(info.DataPath, "config"))
	printSummaryValue("Log file", info.LogPath)
	fmt.Fprintln(os.Stdout)
	fmt.Fprintln(os.Stdout, "Next step")
	if strings.Contains(info.Role, "Data Gateway") {
		fmt.Fprintln(os.Stdout, "  Open HyperFileLens and configure the data sources available")
		fmt.Fprintln(os.Stdout, "  through this Gateway.")
	} else {
		fmt.Fprintln(os.Stdout, "  Open HyperFileLens and configure backup sources and")
		fmt.Fprintln(os.Stdout, "  protection policies.")
	}
	fmt.Fprintln(os.Stdout)
	printAgentLifecycleCommands(info)
}

func printAgentLifecycleCommands(info SummaryInfo) {
	writeAgentLifecycleCommands(os.Stdout, info)
}

func writeAgentLifecycleCommands(writer io.Writer, info SummaryInfo) {
	fmt.Fprintln(writer, "Useful commands")
	printSummaryValueTo(writer, "CLI path", filepath.Join(info.InstallPath, installerScriptName()))
	if runtime.GOOS == "windows" {
		command := windowsPowerShellCommand(filepath.Join(info.InstallPath, installerScriptName()))
		if vfs.UserInstallation() {
			printSummaryValueTo(writer, "Task status", `powershell -NoProfile -Command "$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; Get-ScheduledTask -TaskName ('HyperFileLensAgent.User.'+$sid)"`)
		} else {
			printSummaryValueTo(writer, "Service status", "sc.exe query HyperFileLensAgent")
		}
		printSummaryValueTo(writer, "Agent status", command+" status")
		printSummaryValueTo(writer, "Uninstall", command+" uninstall")
		printSummaryValueTo(writer, "Purge all", command+" uninstall -PurgeAll")
		return
	}

	var lifecycleStatus string
	if runtime.GOOS == "darwin" {
		if vfs.UserInstallation() {
			lifecycleStatus = `launchctl print "gui/$(id -u)/com.hyperfilelens.agent"`
		} else {
			lifecycleStatus = "launchctl print system/com.hyperfilelens.agent"
		}
	} else if vfs.UserInstallation() {
		lifecycleStatus = "systemctl --user status hyperfilelens-agent"
	} else {
		lifecycleStatus = "systemctl status hyperfilelens-agent"
	}
	printSummaryValueTo(writer, "Service status", lifecycleStatus)
	command := strconv.Quote(filepath.Join(info.InstallPath, installerScriptName()))
	if !vfs.UserInstallation() {
		command = "sudo " + command
	}
	printSummaryValueTo(writer, "Agent status", command+" status")
	printSummaryValueTo(writer, "Uninstall", command+" uninstall")
	printSummaryValueTo(writer, "Purge all", command+" uninstall --purge-all")
}

// windowsPowerShellCommand returns a copyable PowerShell invocation for a
// lifecycle script. Backslashes are literal in PowerShell, while apostrophes
// need doubling inside a single-quoted string.
func windowsPowerShellCommand(path string) string {
	return "& '" + strings.ReplaceAll(path, "'", "''") + "'"
}

func printAlreadyEnrolled(info SummaryInfo) {
	if jsonOutput() {
		emitJSON(os.Stdout, map[string]any{
			"type":          "install_result",
			"result":        "unchanged",
			"node_id":       info.NodeID,
			"agent_version": info.Version,
			"agent_service": info.Service,
		})
		return
	}
	printResultRule(os.Stdout, "Existing installation is healthy", ansiGreen)
	fmt.Fprintln(os.Stdout)
	fmt.Fprintln(os.Stdout, "No changes were required.")
	printSummaryValue("Node ID", info.NodeID)
	printSummaryValue("Agent version", info.Version)
	printSummaryValue("Agent service", info.Service)
}

func printResultRule(writer io.Writer, title, color string) {
	fmt.Fprintln(writer)
	fmt.Fprintln(writer, strings.Repeat("=", 64))
	fmt.Fprintln(writer, colorize(ansiBold+color, title))
	fmt.Fprintln(writer, strings.Repeat("=", 64))
}

func printSummaryValue(label, value string) {
	printSummaryValueTo(os.Stdout, label, value)
}

func printSummaryValueTo(writer io.Writer, label, value string) {
	if strings.TrimSpace(value) == "" {
		return
	}
	fmt.Fprintf(writer, "  %-13s %s\n", label, value)
}

func installLogPath() string {
	return filepath.Join(vfs.AgentLogDir(dataDirForAgent()), "install.log")
}

func jsonOutput() bool {
	return strings.EqualFold(strings.TrimSpace(os.Getenv("HFL_OUTPUT")), "json")
}

func parentSession() bool {
	return os.Getenv("HFL_PARENT_SESSION") == "1"
}

func emitJSON(writer io.Writer, payload map[string]any) {
	encoded, err := json.Marshal(payload)
	if err != nil {
		return
	}
	_, _ = fmt.Fprintln(writer, string(encoded))
}
