package cli

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"hyperfilelens/agent/internal/infra/database"
	"hyperfilelens/agent/internal/infra/kopialayout"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/vfs"
)

func runCacheLayout(args []string) error {
	if len(args) == 0 || args[0] != "status" && args[0] != "check-users" {
		return fmt.Errorf("usage: hfl-agent cache-layout status|check-users [--data-dir PATH]")
	}
	fs := flag.NewFlagSet("cache-layout "+args[0], flag.ContinueOnError)
	root := fs.String("data-dir", vfs.DefaultAgentDataDir(), "Agent Root")
	if err := fs.Parse(args[1:]); err != nil {
		return err
	}
	if fs.NArg() != 0 || !filepath.IsAbs(*root) {
		return fmt.Errorf("cache-layout requires an absolute Agent Root")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if args[0] == "status" {
		status, err := kopialayout.Status(*root)
		if err != nil {
			return err
		}
		return json.NewEncoder(os.Stdout).Encode(status)
	}
	if err := kopialayout.CheckUsers(ctx, *root, true); err != nil {
		return err
	}
	path := database.DefaultPath(*root)
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return nil
	} else if err != nil {
		return err
	}
	db, err := database.Open(ctx, path)
	if err != nil {
		return err
	}
	defer db.Close()
	tasks, err := database.NewTaskRepo(db).List(ctx, database.ListFilter{Status: string(model.TaskStatusRunning)})
	if err != nil {
		return err
	}
	for _, task := range tasks {
		kind := strings.ToLower(task.Kind)
		if kind == "agent.upgrade" || kind == "agent.uninstall" {
			continue
		}
		if strings.HasPrefix(kind, "backup") || strings.HasPrefix(kind, "snapshot.") ||
			strings.HasPrefix(kind, "repository.") || strings.HasPrefix(kind, "repo.") ||
			strings.HasPrefix(kind, "lens.") || strings.HasPrefix(kind, "restore") || kind == "maintenance.gc" ||
			task.Payload["repository"] != nil || task.Payload["config_file"] != nil || task.Payload["args"] != nil {
			return fmt.Errorf("CACHE_LAYOUT_BUSY: a repository task is still running; retry upgrade after it exits")
		}
	}
	return nil
}
