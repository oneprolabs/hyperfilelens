package cli

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"

	"hyperfilelens/agent/internal/app"
	"hyperfilelens/agent/internal/engine"
	"hyperfilelens/agent/internal/infra/config"
	"hyperfilelens/agent/internal/infra/database"
	"hyperfilelens/agent/internal/infra/kopialayout"
	"hyperfilelens/agent/internal/model"
)

type cliSink struct {
	asJSON bool
	quiet  bool
}

func (s cliSink) OnProgress(_ context.Context, progress map[string]any) error {
	if s.quiet || s.asJSON {
		return nil
	}
	phase, _ := progress["phase"].(string)
	if phase == "" {
		return nil
	}
	_, _ = fmt.Fprintf(os.Stderr, "progress: %s\n", phase)
	return nil
}

func runEngineCommand(ctx context.Context, store *config.Store, kind string, payload map[string]any, asJSON, quiet bool) error {
	cfg := store.Current()
	dataRoot, logDir, _, err := app.ResolveLayout(cfg)
	if err != nil {
		return err
	}
	db, err := database.Open(ctx, database.DefaultPath(dataRoot))
	if err != nil {
		return fmt.Errorf("open database: %w", err)
	}
	defer db.Close()

	if err := migrateKopiaLayout(ctx, dataRoot, logDir, db); err != nil {
		return err
	}
	repo := database.NewTaskRepo(db)
	taskID, err := newTaskID()
	if err != nil {
		return err
	}
	now := time.Now().UTC()
	if err := repo.RecordCommand(ctx, database.RecordInput{
		TaskID:    taskID,
		Kind:      kind,
		Payload:   payload,
		Source:    string(engine.SourceCLI),
		StartedAt: &now,
	}); err != nil {
		return err
	}

	eng := engine.New(store)
	out := eng.Run(ctx, engine.Command{
		ID:      taskID,
		Kind:    kind,
		Payload: payload,
		Source:  engine.SourceCLI,
	}, cliSink{asJSON: asJSON, quiet: quiet})

	localStatus := model.TaskStatusFailed
	if out.Status == "success" {
		localStatus = model.TaskStatusSucceeded
	}
	if out.Error == "canceled" {
		localStatus = model.TaskStatusCancelled
	}
	if err := repo.Finish(ctx, taskID, localStatus, out.Result, out.Error); err != nil {
		return err
	}

	if asJSON {
		return json.NewEncoder(os.Stdout).Encode(map[string]any{
			"task_id": taskID,
			"status":  out.Status,
			"result":  out.Result,
			"error":   out.Error,
		})
	}

	if out.Status == "success" {
		if out.Result != nil {
			if stdout, ok := out.Result["stdout"].(string); ok && strings.TrimSpace(stdout) != "" {
				fmt.Fprint(os.Stdout, stdout)
				if !strings.HasSuffix(stdout, "\n") {
					fmt.Fprintln(os.Stdout)
				}
			} else {
				b, _ := json.MarshalIndent(out.Result, "", "  ")
				fmt.Println(string(b))
			}
		}
		return nil
	}
	if out.Error != "" {
		return fmt.Errorf("%s", out.Error)
	}
	return fmt.Errorf("command failed")
}

func migrateKopiaLayout(ctx context.Context, root, logDir string, db *database.DB) error {
	// A running service has already completed startup migration. Do not request
	// an exclusive migration lock from a CLI command while it serves tasks.
	ready, err := kopialayout.Ready(root)
	if err != nil || ready {
		return err
	}
	err = kopialayout.Migrate(ctx, root, logDir, database.NewRepoRepo(db).RelocateConfigPaths)
	var incomplete *kopialayout.IncompleteError
	if errors.As(err, &incomplete) {
		fmt.Fprintln(os.Stderr, incomplete.Error())
		return nil
	}
	return err
}

func openConfigStore(ctx context.Context, dataDirOverride string) (*config.Store, error) {
	opts := config.LoadOptions{}
	if dataDirOverride = strings.TrimSpace(dataDirOverride); dataDirOverride != "" {
		opts.Overrides.DataDir = dataDirOverride
	}
	return config.NewStore(ctx, opts)
}

func newTaskID() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(b[:]), nil
}
