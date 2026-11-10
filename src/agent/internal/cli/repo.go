package cli

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"strings"
	"text/tabwriter"

	"hyperfilelens/agent/internal/app"
	"hyperfilelens/agent/internal/infra/database"
	"hyperfilelens/agent/internal/model"
)

func resolveKopiaConfig(ctx context.Context, dataDir, repoName, configFile string) (string, error) {
	repoName = strings.TrimSpace(repoName)
	configFile = strings.TrimSpace(configFile)
	switch {
	case repoName != "" && configFile != "":
		return "", fmt.Errorf("use only one of --repo or --config-file")
	case repoName == "" && configFile == "":
		return "", fmt.Errorf("one of --repo or --config-file is required")
	case configFile != "":
		// Preserve the historical external-config fast path. The engine applies
		// the managed legacy alias after it has opened the installation and run
		// migration; standalone callers must not be forced to initialize an
		// Agent database or cache merely to use their own Kopia config.
		return configFile, nil
	}

	store, err := openConfigStore(ctx, dataDir)
	if err != nil {
		return "", err
	}
	cfg := store.Current()
	dataRoot, _, _, err := app.ResolveLayout(cfg)
	if err != nil {
		return "", err
	}
	db, err := database.Open(ctx, database.DefaultPath(dataRoot))
	if err != nil {
		return "", err
	}
	defer db.Close()

	repo, err := database.NewRepoRepo(db).Get(ctx, repoName)
	if err != nil {
		return "", err
	}
	return repo.ConfigFile, nil
}

func openRepoDB(ctx context.Context, dataDir string) (*database.DB, *database.RepoRepo, error) {
	store, err := openConfigStore(ctx, dataDir)
	if err != nil {
		return nil, nil, err
	}
	cfg := store.Current()
	dataRoot, logDir, _, err := app.ResolveLayout(cfg)
	if err != nil {
		return nil, nil, err
	}
	db, err := database.Open(ctx, database.DefaultPath(dataRoot))
	if err != nil {
		return nil, nil, fmt.Errorf("open database: %w", err)
	}
	if err := migrateKopiaLayout(ctx, dataRoot, logDir, db); err != nil {
		_ = db.Close()
		return nil, nil, err
	}
	return db, database.NewRepoRepo(db), nil
}

func repoNotFoundHint(err error) string {
	if errors.Is(err, database.ErrRepoNotFound) {
		return " (try: hfl-agent repo list)"
	}
	return ""
}

func isValidRepoName(name string) error {
	name = strings.TrimSpace(name)
	if name == "" {
		return fmt.Errorf("empty repo name")
	}
	if strings.ContainsAny(name, " \t/\\") {
		return fmt.Errorf("invalid repo name %q", name)
	}
	return nil
}

func printRepoList(repos []model.Repo, asJSON bool) error {
	if asJSON {
		return json.NewEncoder(os.Stdout).Encode(map[string]any{"repos": repos})
	}
	if len(repos) == 0 {
		fmt.Fprintln(os.Stdout, "no repos registered (try: hfl-agent repo connect <name> --config-file PATH)")
		return nil
	}
	w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
	_, _ = fmt.Fprintln(w, "NAME\tCONFIG_FILE\tDESCRIPTION")
	for _, r := range repos {
		_, _ = fmt.Fprintf(w, "%s\t%s\t%s\n", r.Name, r.ConfigFile, r.Description)
	}
	_ = w.Flush()
	fmt.Fprintf(os.Stdout, "\n%d repo(s)\n", len(repos))
	return nil
}

func runRepo(ctx context.Context, args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: hfl-agent repo list|connect|disconnect|show")
	}
	switch args[0] {
	case "list", "ls":
		return repoList(ctx, args[1:])
	case "connect", "add":
		return repoConnect(ctx, args[1:])
	case "disconnect", "rm", "remove":
		return repoDisconnect(ctx, args[1:])
	case "show", "get":
		return repoShow(ctx, args[1:])
	default:
		return fmt.Errorf("unknown repo command %q", args[0])
	}
}

func repoList(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("repo list", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	var (
		asJSON  bool
		dataDir string
	)
	fs.BoolVar(&asJSON, "json", false, "print JSON")
	fs.StringVar(&dataDir, "data-dir", "", "override HFL_DATA_DIR")
	if err := fs.Parse(args); err != nil {
		return err
	}

	db, repos, err := openRepoDB(ctx, dataDir)
	if err != nil {
		return err
	}
	defer db.Close()

	list, err := repos.List(ctx)
	if err != nil {
		return err
	}
	return printRepoList(list, asJSON)
}

func repoConnect(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("repo connect", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	var (
		configFile  string
		description string
		dataDir     string
	)
	fs.StringVar(&configFile, "config-file", "", "kopia repository config file")
	fs.StringVar(&description, "description", "", "optional note")
	fs.StringVar(&dataDir, "data-dir", "", "override HFL_DATA_DIR")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 1 {
		return fmt.Errorf("usage: hfl-agent repo connect <name> --config-file PATH")
	}
	name := fs.Arg(0)
	if err := isValidRepoName(name); err != nil {
		return err
	}
	if configFile == "" {
		return fmt.Errorf("--config-file is required")
	}

	db, repos, err := openRepoDB(ctx, dataDir)
	if err != nil {
		return err
	}
	defer db.Close()

	if err := repos.Connect(ctx, database.ConnectInput{
		Name:        name,
		ConfigFile:  configFile,
		Description: description,
	}); err != nil {
		return err
	}
	fmt.Fprintf(os.Stdout, "connected repo %q -> %s\n", name, configFile)
	return nil
}

func repoDisconnect(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("repo disconnect", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	var dataDir string
	fs.StringVar(&dataDir, "data-dir", "", "override HFL_DATA_DIR")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 1 {
		return fmt.Errorf("usage: hfl-agent repo disconnect <name>")
	}
	if err := isValidRepoName(fs.Arg(0)); err != nil {
		return err
	}

	db, repos, err := openRepoDB(ctx, dataDir)
	if err != nil {
		return err
	}
	defer db.Close()

	if err := repos.Disconnect(ctx, fs.Arg(0)); err != nil {
		return err
	}
	fmt.Fprintf(os.Stdout, "disconnected repo %q\n", fs.Arg(0))
	return nil
}

func repoShow(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("repo show", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	var (
		asJSON  bool
		verify  bool
		dataDir string
	)
	fs.BoolVar(&asJSON, "json", false, "print JSON")
	fs.BoolVar(&verify, "verify", false, "run kopia repository status")
	fs.StringVar(&dataDir, "data-dir", "", "override HFL_DATA_DIR")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 1 {
		return fmt.Errorf("usage: hfl-agent repo show <name> [--verify]")
	}

	db, repos, err := openRepoDB(ctx, dataDir)
	if err != nil {
		return err
	}
	defer db.Close()

	repo, err := repos.Get(ctx, fs.Arg(0))
	if err != nil {
		return fmt.Errorf("%w%s", err, repoNotFoundHint(err))
	}

	if verify {
		store, err := openConfigStore(ctx, dataDir)
		if err != nil {
			return err
		}
		return runEngineCommand(ctx, store, "repo.status", map[string]any{
			"config_file": repo.ConfigFile,
		}, asJSON, false)
	}

	if asJSON {
		return json.NewEncoder(os.Stdout).Encode(repo)
	}
	fmt.Fprintf(os.Stdout, "name:         %s\n", repo.Name)
	fmt.Fprintf(os.Stdout, "config_file:  %s\n", repo.ConfigFile)
	if repo.Description != "" {
		fmt.Fprintf(os.Stdout, "description:  %s\n", repo.Description)
	}
	return nil
}
