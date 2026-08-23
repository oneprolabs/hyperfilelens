package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"syscall"

	"hyperfilelens/agent/internal/app"
	"hyperfilelens/agent/internal/cli"
	"hyperfilelens/agent/internal/infra/config"
	"hyperfilelens/agent/internal/infra/observability"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/svcwrap"
	"hyperfilelens/agent/internal/selfupdate"
)

func main() {
	args := os.Args[1:]
	if len(args) > 0 && cli.IsSubcommand(args[0]) {
		if err := cli.Run(args); err != nil {
			_, _ = fmt.Fprintf(os.Stderr, "error: %v\n", err)
			os.Exit(2)
		}
		return
	}
	shutdownSentry := observability.Initialize()
	defer shutdownSentry()
	defer observability.RecoverPanic()

	// Explicit `run` subcommand or bare flags / no args (systemd compatibility).
	if len(args) > 0 && args[0] == "run" {
		args = args[1:]
	}
	if err := runDaemon(args); err != nil {
		observability.CaptureException(err)
		_, _ = fmt.Fprintf(os.Stderr, "%s\n", err)
		// os.Exit skips deferred functions, so flush the fatal event explicitly.
		shutdownSentry()
		os.Exit(1)
	}
}

func runDaemon(args []string) error {
	fs := flag.NewFlagSet("hfl-agent run", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	var (
		printVersion = fs.Bool("version", false, "print version and exit")
		printConfig  = fs.Bool("print-config", false, "print effective configuration and exit")
		wssURL       = fs.String("wss-url", "", "control plane WebSocket URL (overrides HFL_WSS_URL)")
		apiBase      = fs.String("api-base", "", "HTTPS API base for bootstrap (overrides HFL_API_BASE)")
		dataDir      = fs.String("data-dir", "", "unified Agent root (overrides HFL_DATA_DIR)")
		logDir       = fs.String("log-dir", "", "rolling log directory (overrides HFL_LOG_DIR; default: <data>/logs)")
		kopiaPath    = fs.String("kopia-path", "", "path to kopia CLI (overrides HFL_KOPIA_PATH)")
		orgKey       = fs.String("org-key", "", "organization key (overrides HFL_ORG_KEY)")
		nodeID       = fs.String("node-id", "", "node id if already known (overrides HFL_NODE_ID)")
		nodeToken    = fs.String("node-token", "", "enrollment node token (overrides HFL_NODE_TOKEN)")
		roleFlag     = fs.String("role", "", "agent role: agent|proxy|gateway")
	)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 0 {
		return fmt.Errorf(
			"unexpected daemon argument %q; use 'hfl-agent help' for supported commands",
			fs.Arg(0),
		)
	}

	if *printVersion {
		fmt.Printf("hyperfilelens-agent %s (%s)\n", selfupdate.Version, selfupdate.Commit)
		return nil
	}

	overrides, err := buildOverrides(*wssURL, *apiBase, *dataDir, *logDir, *kopiaPath, *orgKey, *nodeID, *nodeToken, *roleFlag)
	if err != nil {
		return err
	}

	store, err := config.NewStore(context.Background(), config.LoadOptions{Overrides: overrides})
	if err != nil {
		return fmt.Errorf("config: %w", err)
	}

	if *printConfig {
		text, err := config.DumpText(store.Current())
		if err != nil {
			return err
		}
		envPath, jsonPath := store.Paths()
		fmt.Print(text)
		fmt.Printf("env_file=%q\njson_file=%q\n", envPath, jsonPath)
		return nil
	}

	run := func(ctx context.Context) error {
		cfg := store.Current()
		if err := app.InitRuntimeDirsAndLogging(cfg); err != nil {
			return fmt.Errorf("init runtime dirs/logging: %w", err)
		}
		agent := app.New(store)
		go store.Watch(ctx, 0)
		if err := agent.Run(ctx); err != nil && err != context.Canceled {
			return err
		}
		return nil
	}

	if handled, err := svcwrap.RunIfService(run); handled {
		return err
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	return run(ctx)
}

func buildOverrides(
	wssURL, apiBase, dataDir, logDir, kopiaPath, orgKey, nodeID, nodeToken, roleFlag string,
) (config.Overrides, error) {
	o := config.Overrides{
		WSSURL:     strings.TrimSpace(wssURL),
		APIBaseURL: strings.TrimSpace(apiBase),
		OrgKey:     strings.TrimSpace(orgKey),
		NodeID:     strings.TrimSpace(nodeID),
		NodeToken:  strings.TrimSpace(nodeToken),
		DataDir:    strings.TrimSpace(dataDir),
		LogDir:     strings.TrimSpace(logDir),
		KopiaPath:  strings.TrimSpace(kopiaPath),
	}
	if strings.TrimSpace(roleFlag) != "" {
		role, err := model.ParseRole(roleFlag)
		if err != nil {
			return o, err
		}
		o.Role = role
	}
	return o, nil
}
