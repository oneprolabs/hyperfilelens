package cli

import (
	"context"
	"fmt"
	"os"
	"strings"

	"hyperfilelens/agent/internal/infra/config"
	"hyperfilelens/agent/internal/selfupdate"
)

// Run dispatches `hfl-agent <command> ...` subcommands.
func Run(args []string) error {
	if len(args) == 0 || args[0] == "help" || args[0] == "-h" || args[0] == "--help" {
		printRootHelp()
		return nil
	}
	switch args[0] {
	case "version":
		if len(args) != 1 {
			return fmt.Errorf("version does not accept arguments")
		}
		_, _ = fmt.Fprintf(
			os.Stdout,
			"hyperfilelens-agent %s (%s)\n",
			selfupdate.Version,
			selfupdate.Commit,
		)
		return nil
	case "config":
		return config.RunCLI(context.Background(), args[1:])
	case "tasks":
		return runTasks(context.Background(), args[1:])
	case "fs":
		return runFS(context.Background(), args[1:])
	case "snapshot":
		return runSnapshot(context.Background(), args[1:])
	case "restore":
		return runRestore(context.Background(), args[1:])
	case "repo":
		return runRepo(context.Background(), args[1:])
	default:
		return fmt.Errorf("unknown command %q (try: hfl-agent help)", args[0])
	}
}

func printRootHelp() {
	const help = `HyperFileLens Agent CLI

Usage:
  hfl-agent run [flags]             Run agent daemon (WebSocket control plane)
  hfl-agent version                 Print version and exit
  hfl-agent help                    Show this help
  hfl-agent config show|set|paths   Manage hot-reloadable configuration
  hfl-agent fs ls [path]            List local directory entries
  hfl-agent snapshot list|create    Kopia snapshot operations
  hfl-agent restore <path>          Restore from Kopia snapshot
  hfl-agent repo list|connect|...   Manage registered Kopia repo aliases
  hfl-agent tasks ...               Inspect/update local task DB and report via WSS

Daemon:
  hfl-agent run                     Long-running service (systemd / Windows Service)
  hfl-agent run -print-config       Print effective config and exit

Local operations (short-lived; do not require daemon):
  hfl-agent fs ls /data
  hfl-agent repo connect main --config-file /path/to/kopia.config
  hfl-agent snapshot list --repo main
  hfl-agent snapshot create /data/foo --repo main
  hfl-agent restore /restore/here --repo main --snapshot latest

Config:
  hfl-agent config show
  hfl-agent config set HFL_WSS_URL=wss://host/ws/node/agent/
  hfl-agent config paths
  hfl-agent config retire-installation --data-dir PATH

Repo registry (local SQLite at {HFL_DATA_DIR}/data/agent.db):
  hfl-agent repo list [--json]
  hfl-agent repo connect <name> --config-file PATH [--description TEXT]
  hfl-agent repo disconnect <name>
  hfl-agent repo show <name> [--verify] [--json]

Tasks (local SQLite at {HFL_DATA_DIR}/data/agent.db):
  hfl-agent tasks list [--status running|failed|...] [--unreported] [--json] [--limit N]
  hfl-agent tasks get <task-id> [--json]
  hfl-agent tasks set <task-id> --status failed --error "reason" [--result-json '{}'] [--reported true|false]
  hfl-agent tasks report <task-id> [--status success|failed] [--error msg] [--result-json '{}'] [--mark-reported]
  hfl-agent tasks progress <task-id> [--json '{"phase":"running"}']   Send task.progress via WebSocket
  hfl-agent tasks flush [--mark-reported]   Report all unreported terminal tasks via WebSocket

Environment:
  Uses HFL_* variables and {HFL_DATA_DIR}/config/agent.env (same as the daemon).
  HFL_WSS_URL is optional at install; daemon waits idle until configured.
  Kopia CLI must be installed (PATH or HFL_KOPIA_PATH).
`
	_, _ = fmt.Fprint(os.Stdout, help)
}

func parseStatus(s string) (string, error) {
	s = strings.ToLower(strings.TrimSpace(s))
	if s == "" {
		return "", nil
	}
	switch s {
	case "pending", "running", "succeeded", "failed", "cancelled", "success":
		if s == "success" {
			return "succeeded", nil
		}
		return s, nil
	default:
		return "", fmt.Errorf("invalid status %q", s)
	}
}

func parseWireStatus(s string) (string, error) {
	s = strings.ToLower(strings.TrimSpace(s))
	switch s {
	case "success", "succeeded":
		return "success", nil
	case "failed", "fail":
		return "failed", nil
	case "":
		return "", nil
	default:
		return "", fmt.Errorf("invalid wire status %q (use success or failed)", s)
	}
}

// IsSubcommand reports whether arg is a CLI subcommand (not daemon flags).
func IsSubcommand(arg string) bool {
	switch arg {
	case "help", "-h", "--help", "version", "config", "tasks", "fs", "snapshot", "restore", "repo":
		return true
	default:
		return false
	}
}
