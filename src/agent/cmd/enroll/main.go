package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"strings"

	"hyperfilelens/agent/internal/enroll"
)

func main() {
	removeBootstrapTempBinary()
	finishLogging := enroll.StartCommandLogging()
	exitCode := func() int {
		defer finishLogging()
		return run()
	}()
	if exitCode != 0 {
		os.Exit(exitCode)
	}
}

func run() int {
	if len(os.Args) < 2 {
		printHelp()
		return 0
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	switch os.Args[1] {
	case "install":
		stabilizeInstallWorkingDirectory()
		opts := enroll.ParseInstallOptions(os.Args[2:])
		if err := enroll.WithInstallLock(ctx, func() error {
			return enroll.RunInstall(ctx, opts)
		}); err != nil {
			enroll.PrintCommandFailureFor(installFailureOperation(opts.Mode), err)
			return 1
		}
	case "gateway-install":
		stabilizeInstallWorkingDirectory()
		opts := enroll.ParseInstallOptions(os.Args[2:])
		if err := enroll.WithInstallLock(ctx, func() error {
			return enroll.RunGatewayInstall(ctx, opts)
		}); err != nil {
			enroll.PrintCommandFailureFor(installFailureOperation(opts.Mode), err)
			return 1
		}
	case "gateway-upgrade":
		stabilizeInstallWorkingDirectory()
		fromArchive := parseFromFlag(os.Args[2:])
		if err := enroll.WithInstallLock(ctx, func() error {
			return enroll.RunGatewayUpgrade(ctx, fromArchive)
		}); err != nil {
			enroll.PrintCommandFailureFor("upgrade", err)
			return 1
		}
	case "gateway-uninstall":
		stabilizeInstallWorkingDirectory()
		purgeAll := !hasFlag(os.Args[2:], "--keep-data")
		if err := enroll.WithInstallLock(ctx, func() error {
			return enroll.RunGatewayUninstall(ctx, purgeAll)
		}); err != nil {
			enroll.PrintCommandFailureFor("uninstall", err)
			return 1
		}
	case "register":
		stabilizeInstallWorkingDirectory()
		opts := enroll.ParseInstallOptions(os.Args[2:])
		if err := enroll.WithInstallLock(ctx, func() error {
			return enroll.RunRegister(ctx, opts)
		}); err != nil {
			enroll.PrintCommandFailureFor("registration", err)
			return 1
		}
	case "status":
		if err := enroll.RunCommand(func() error {
			return enroll.RunStatus(ctx)
		}); err != nil {
			enroll.PrintCommandFailureFor("status", err)
			return 1
		}
	case "help", "-h", "--help":
		printHelp()
	default:
		fmt.Fprintf(os.Stderr, "unknown command %q (try: hfl-enroll help)\n", os.Args[1])
		return 2
	}
	return 0
}

func printHelp() {
	fmt.Print(`HyperFileLens enrollment tool

Usage:
  hfl-enroll install [--yes|-y]           Install or safely inspect an existing Agent
  hfl-enroll install --upgrade            Upgrade an existing Agent to the console release
  hfl-enroll install --repair             Repair configuration and restart an existing Agent
  hfl-enroll install --reinstall          Reinstall the console release over an existing Agent
  hfl-enroll install --uninstall          Uninstall the Agent and preserve its data
  hfl-enroll gateway-install [--yes|-y]   Public or Private Data Gateway (Linux)
  hfl-enroll gateway-upgrade [--from PATH] Upgrade Agent and AI engine (Linux)
  hfl-enroll gateway-uninstall [--keep-data] Remove AI engine and Agent (default: purge-all)
  hfl-enroll register [--yes|-y]          HTTP heartbeat registration only (agent installed)
  hfl-enroll status                       Show node_id and service state
  hfl-enroll help                         Show this help

Flags:
  --yes, -y    Skip confirmation prompts (repair, upgrade, re-bind on existing install)
  --output MODE  auto | rich | plain | json
  --no-color     Disable ANSI colors
  --no-banner    Hide the HyperFileLens banner
  --verbose      Include detailed diagnostic output
  --purge-all    With --uninstall, also remove managed Agent data

Environment (set by bootstrap stub from console):
  HFL_ORG_KEY, HFL_NODE_ROLE, HFL_NODE_TOKEN, HFL_API_BASE, HFL_WSS_URL
  HFL_INSECURE_TLS       Default 1 (skip TLS verify for dev/self-signed)
`)
}

// stabilizeInstallWorkingDirectory moves off a deleted install path so the shell
// and child tools do not spam getcwd / job-working-directory errors.
func stabilizeInstallWorkingDirectory() {
	if err := os.Chdir("/"); err == nil {
		return
	}
	_ = os.Chdir(os.TempDir())
}

func parseFromFlag(args []string) string {
	for i, arg := range args {
		if arg == "--from" && i+1 < len(args) {
			return strings.TrimSpace(args[i+1])
		}
		if strings.HasPrefix(arg, "--from=") {
			return strings.TrimSpace(strings.TrimPrefix(arg, "--from="))
		}
	}
	return ""
}

func hasFlag(args []string, name string) bool {
	for _, arg := range args {
		if arg == name {
			return true
		}
	}
	return false
}

func installFailureOperation(mode enroll.InstallMode) string {
	switch mode {
	case enroll.InstallModeUpgrade:
		return "upgrade"
	case enroll.InstallModeUninstall:
		return "uninstall"
	default:
		return "install"
	}
}
