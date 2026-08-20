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
	"time"

	"hyperfilelens/agent/internal/infra/database"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/remote"
	"hyperfilelens/agent/internal/wire"
)

func runTasks(ctx context.Context, args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: hfl-agent tasks list|get|set|report|flush")
	}
	switch args[0] {
	case "list", "ls":
		return tasksList(ctx, args[1:])
	case "get":
		return tasksGet(ctx, args[1:])
	case "set":
		return tasksSet(ctx, args[1:])
	case "report":
		return tasksReport(ctx, args[1:])
	case "progress":
		return tasksProgress(ctx, args[1:])
	case "flush":
		return tasksFlush(ctx, args[1:])
	default:
		return fmt.Errorf("unknown tasks command %q", args[0])
	}
}

func tasksList(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("tasks list", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	var (
		status     string
		unreported bool
		limit      int
		asJSON     bool
		dataDir    string
	)
	fs.StringVar(&status, "status", "", "filter by status: pending|running|succeeded|failed|cancelled")
	fs.BoolVar(&unreported, "unreported", false, "only terminal tasks not yet reported upstream")
	fs.IntVar(&limit, "limit", 50, "max rows")
	fs.BoolVar(&asJSON, "json", false, "print JSON")
	fs.StringVar(&dataDir, "data-dir", "", "override HFL_DATA_DIR")
	if err := fs.Parse(args); err != nil {
		return err
	}

	rt, err := openRuntime(ctx, dataDir)
	if err != nil {
		return err
	}
	defer rt.close()

	if status, err = parseStatus(status); err != nil {
		return err
	}
	tasks, err := rt.tasks.List(ctx, database.ListFilter{
		Status:         status,
		UnreportedOnly: unreported,
		Limit:          limit,
	})
	if err != nil {
		return err
	}

	if asJSON {
		return json.NewEncoder(os.Stdout).Encode(map[string]any{
			"db_path": rt.dbPath(),
			"tasks":   tasks,
		})
	}

	fmt.Fprintf(os.Stdout, "database: %s\n\n", rt.dbPath())
	w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
	_, _ = fmt.Fprintln(w, "ID\tKIND\tSOURCE\tSTATUS\tREPORTED\tERROR")
	for _, t := range tasks {
		reported := "no"
		if t.ResultReported {
			reported = "yes"
		}
		errShort := t.Error
		if len(errShort) > 40 {
			errShort = errShort[:37] + "..."
		}
		source := t.Source
		if source == "" {
			source = "websocket"
		}
		_, _ = fmt.Fprintf(w, "%s\t%s\t%s\t%s\t%s\t%s\n", t.ID, t.Kind, source, t.Status, reported, errShort)
	}
	_ = w.Flush()
	fmt.Fprintf(os.Stdout, "\n%d task(s)\n", len(tasks))
	return nil
}

func tasksGet(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("tasks get", flag.ContinueOnError)
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
	if fs.NArg() != 1 {
		return fmt.Errorf("usage: hfl-agent tasks get <task-id> [--json]")
	}

	rt, err := openRuntime(ctx, dataDir)
	if err != nil {
		return err
	}
	defer rt.close()

	task, err := rt.tasks.Get(ctx, fs.Arg(0))
	if err != nil {
		return err
	}
	if asJSON {
		return json.NewEncoder(os.Stdout).Encode(task)
	}
	printTaskHuman(task, rt.dbPath())
	return nil
}

func tasksSet(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("tasks set", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	var (
		statusStr  string
		errorMsg   string
		resultJSON string
		reported   string
		kind       string
		jobID      string
		dataDir    string
	)
	fs.StringVar(&statusStr, "status", "", "pending|running|succeeded|failed|cancelled")
	fs.StringVar(&errorMsg, "error", "", "error message")
	fs.StringVar(&resultJSON, "result-json", "", "result object JSON or @file.json")
	fs.StringVar(&reported, "reported", "", "true|false — result_reported flag")
	fs.StringVar(&kind, "kind", "", "task kind")
	fs.StringVar(&jobID, "job-id", "", "job / correlation id")
	fs.StringVar(&dataDir, "data-dir", "", "override HFL_DATA_DIR")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 1 {
		return fmt.Errorf("usage: hfl-agent tasks set <task-id> [--status ...] [--error ...]")
	}

	rt, err := openRuntime(ctx, dataDir)
	if err != nil {
		return err
	}
	defer rt.close()

	patch := database.UpdateInput{}
	if statusStr != "" {
		st, err := parseStatus(statusStr)
		if err != nil {
			return err
		}
		ms := model.TaskStatus(st)
		patch.Status = &ms
	}
	if errorMsg != "" {
		patch.Error = &errorMsg
	}
	if resultJSON != "" {
		res, err := loadResultJSON(resultJSON)
		if err != nil {
			return err
		}
		patch.Result = res
	}
	if kind != "" {
		patch.Kind = &kind
	}
	if jobID != "" {
		patch.JobID = &jobID
	}
	if reported != "" {
		v := strings.EqualFold(reported, "true") || reported == "1"
		patch.ResultReported = &v
	}

	if err := rt.tasks.Update(ctx, fs.Arg(0), patch); err != nil {
		return err
	}
	task, err := rt.tasks.Get(ctx, fs.Arg(0))
	if err != nil {
		return err
	}
	fmt.Fprintf(os.Stdout, "updated %s in %s\n", task.ID, rt.dbPath())
	printTaskHuman(task, "")
	return nil
}

func tasksReport(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("tasks report", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	var (
		wireStatus string
		errorMsg   string
		resultJSON string
		mark       bool
		dataDir    string
	)
	fs.StringVar(&wireStatus, "status", "", "success|failed (default from local DB)")
	fs.StringVar(&errorMsg, "error", "", "override error message")
	fs.StringVar(&resultJSON, "result-json", "", "override result JSON or @file")
	fs.BoolVar(&mark, "mark-reported", true, "set result_reported=1 in local DB after send")
	fs.StringVar(&dataDir, "data-dir", "", "override HFL_DATA_DIR")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 1 {
		return fmt.Errorf("usage: hfl-agent tasks report <task-id> [--status success|failed]")
	}

	rt, err := openRuntime(ctx, dataDir)
	if err != nil {
		return err
	}
	defer rt.close()

	taskID := fs.Arg(0)
	var task model.Task
	t, err := rt.tasks.Get(ctx, taskID)
	if err != nil {
		if !errors.Is(err, database.ErrTaskNotFound) || wireStatus == "" {
			return err
		}
		task = model.Task{ID: taskID}
	} else {
		task = t
	}

	status, err := resolveWireReport(&task, wireStatus, errorMsg, resultJSON)
	if err != nil {
		return err
	}

	if err := sendTaskResult(ctx, rt, task.ID, status.wire, status.result, status.errMsg); err != nil {
		return err
	}
	fmt.Fprintf(os.Stdout, "reported task.result task_id=%s status=%s\n", task.ID, status.wire)

	if mark {
		if err := rt.tasks.MarkResultReported(ctx, task.ID); err != nil {
			return err
		}
	}
	return nil
}

func tasksProgress(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("tasks progress", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	var (
		progressJSON string
		dataDir      string
	)
	fs.StringVar(&progressJSON, "json", `{"phase":"running","status":"running"}`, "progress object JSON or @file")
	fs.StringVar(&dataDir, "data-dir", "", "override HFL_DATA_DIR")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 1 {
		return fmt.Errorf("usage: hfl-agent tasks progress <task-id> [--json '{...}']")
	}

	rt, err := openRuntime(ctx, dataDir)
	if err != nil {
		return err
	}
	defer rt.close()

	progress, err := loadResultJSON(progressJSON)
	if err != nil {
		return err
	}

	dialCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	session, err := remote.Connect(dialCtx, rt.store)
	if err != nil {
		return fmt.Errorf("websocket connect: %w", err)
	}
	defer session.Close()

	sendCtx, sendCancel := context.WithTimeout(ctx, 10*time.Second)
	defer sendCancel()
	if err := wire.SendTaskProgress(sendCtx, session, fs.Arg(0), progress); err != nil {
		return fmt.Errorf("send task.progress: %w", err)
	}
	fmt.Fprintf(os.Stdout, "reported task.progress task_id=%s\n", fs.Arg(0))
	return nil
}

func tasksFlush(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("tasks flush", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	var (
		mark    bool
		dataDir string
	)
	fs.BoolVar(&mark, "mark-reported", true, "set result_reported=1 after each successful send")
	fs.StringVar(&dataDir, "data-dir", "", "override HFL_DATA_DIR")
	if err := fs.Parse(args); err != nil {
		return err
	}

	rt, err := openRuntime(ctx, dataDir)
	if err != nil {
		return err
	}
	defer rt.close()

	pending, err := rt.tasks.ListUnreported(ctx, 0)
	if err != nil {
		return err
	}
	if len(pending) == 0 {
		fmt.Fprintln(os.Stdout, "no unreported tasks")
		return nil
	}

	sent := 0
	for _, task := range pending {
		wireStatus := database.WireStatus(task.Status)
		errMsg := task.Error
		if errMsg == "" && wireStatus == "failed" {
			errMsg = string(task.Status)
		}
		if err := sendTaskResult(ctx, rt, task.ID, wireStatus, task.Result, errMsg); err != nil {
			fmt.Fprintf(os.Stderr, "skip %s: %v\n", task.ID, err)
			continue
		}
		if mark {
			_ = rt.tasks.MarkResultReported(ctx, task.ID)
		}
		fmt.Fprintf(os.Stdout, "reported %s status=%s\n", task.ID, wireStatus)
		sent++
	}
	fmt.Fprintf(os.Stdout, "flushed %d/%d task(s)\n", sent, len(pending))
	return nil
}

type reportPayload struct {
	wire   string
	result map[string]any
	errMsg string
}

func resolveWireReport(task *model.Task, wireStatus, errorMsg, resultJSON string) (reportPayload, error) {
	out := reportPayload{
		wire:   database.WireStatus(task.Status),
		result: task.Result,
		errMsg: task.Error,
	}
	if wireStatus != "" {
		ws, err := parseWireStatus(wireStatus)
		if err != nil {
			return out, err
		}
		out.wire = ws
	}
	if errorMsg != "" {
		out.errMsg = errorMsg
	}
	if resultJSON != "" {
		res, err := loadResultJSON(resultJSON)
		if err != nil {
			return out, err
		}
		out.result = res
	}
	if out.result == nil {
		out.result = map[string]any{}
	}
	return out, nil
}

func sendTaskResult(ctx context.Context, rt *runtime, taskID, status string, result map[string]any, errMsg string) error {
	dialCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()

	session, err := remote.Connect(dialCtx, rt.store)
	if err != nil {
		return fmt.Errorf("websocket connect: %w", err)
	}
	defer session.Close()

	sendCtx, sendCancel := context.WithTimeout(ctx, 10*time.Second)
	defer sendCancel()

	if err := wire.SendTaskResult(sendCtx, session, taskID, status, result, errMsg); err != nil {
		return fmt.Errorf("send task.result: %w", err)
	}
	return nil
}

func printTaskHuman(task model.Task, dbPath string) {
	if dbPath != "" {
		fmt.Fprintf(os.Stdout, "database: %s\n", dbPath)
	}
	fmt.Fprintf(os.Stdout, "id:              %s\n", task.ID)
	fmt.Fprintf(os.Stdout, "job_id:          %s\n", task.JobID)
	fmt.Fprintf(os.Stdout, "kind:            %s\n", task.Kind)
	if task.Source != "" {
		fmt.Fprintf(os.Stdout, "source:          %s\n", task.Source)
	}
	fmt.Fprintf(os.Stdout, "status:          %s\n", task.Status)
	fmt.Fprintf(os.Stdout, "result_reported: %v\n", task.ResultReported)
	if task.Error != "" {
		fmt.Fprintf(os.Stdout, "error:           %s\n", task.Error)
	}
	if task.StartedAt != nil {
		fmt.Fprintf(os.Stdout, "started_at:      %s\n", task.StartedAt.Format(time.RFC3339))
	}
	if task.FinishedAt != nil {
		fmt.Fprintf(os.Stdout, "finished_at:     %s\n", task.FinishedAt.Format(time.RFC3339))
	}
	if len(task.Payload) > 0 {
		b, _ := json.MarshalIndent(task.Payload, "", "  ")
		fmt.Fprintf(os.Stdout, "payload:\n%s\n", string(b))
	}
	if len(task.Result) > 0 {
		b, _ := json.MarshalIndent(task.Result, "", "  ")
		fmt.Fprintf(os.Stdout, "result:\n%s\n", string(b))
	}
}
