package engine

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"os"
	"strings"
	"sync"

	"hyperfilelens/agent/internal/infra/config"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/disk"
	"hyperfilelens/agent/internal/platform/kopia"
	"hyperfilelens/agent/internal/platform/pathsize"
	"hyperfilelens/agent/internal/platform/process"
	"hyperfilelens/agent/internal/selfupdate"
	"hyperfilelens/agent/internal/service/explorer"
	"hyperfilelens/agent/internal/service/nas"
)

// Engine runs backup, browse, and maintenance workloads for WebSocket and CLI entrypoints.
type Engine struct {
	provider              config.Provider
	activeMu              sync.Mutex
	activeKill            func()
	repositoryServerMu    sync.Mutex
	repositoryServerPorts map[int]struct{}
}

// New returns an engine that reads config from provider on each command.
func New(provider config.Provider) *Engine {
	return &Engine{provider: provider}
}

func (e *Engine) current() *model.AgentConfig {
	if e.provider != nil {
		return e.provider.Current()
	}
	return &model.AgentConfig{}
}

// Run executes a command and reports progress via sink when provided.
func (e *Engine) Run(ctx context.Context, cmd Command, sink ExecutionSink) Result {
	if err := ctx.Err(); err != nil {
		return Result{Status: "failed", Error: "canceled"}
	}

	kind := NormalizeKind(cmd.Kind)
	p := ParsePayload(cmd.Payload)
	rep := ReporterSink{Sink: sink, TaskID: cmd.ID}
	nodeID := e.current().NodeID
	slog.InfoContext(ctx, "engine task started", "node_id", nodeID, "task_id", cmd.ID, "kind", kind)

	var status string
	var result map[string]any
	var errMsg string

	switch kind {
	case "browse":
		status, result, errMsg = e.runBrowse(ctx, rep, cmd.ID, p)
	case "lens.gateway.browse":
		status, result, errMsg = e.runLensGatewayBrowse(ctx, p)
	case "lens.workspace.validate-local":
		status, result, errMsg = e.runLensWorkspaceValidateLocal(ctx, p)
	case "backup":
		if _, ok, parseErr := parseRepositorySpec(p.Extra["repository"]); parseErr != nil {
			status, result, errMsg = "failed", nil, parseErr.Error()
			break
		} else if ok {
			status, result, errMsg = e.runManagedBackup(ctx, rep, cmd.ID, p)
			break
		}
		def := []string{"snapshot", "create"}
		if p.Path != "" {
			def = append(def, p.Path)
		}
		status, result, errMsg = e.runKopia(ctx, rep, cmd.ID, p, def)
	case "backup.snapshot.create":
		if _, ok, parseErr := parseRepositorySpec(p.Extra["repository"]); parseErr != nil {
			status, result, errMsg = "failed", nil, parseErr.Error()
			break
		} else if ok {
			status, result, errMsg = e.runManagedPreparedSnapshot(ctx, rep, cmd.ID, p)
			break
		}
		status, result, errMsg = "failed", nil, "repository payload is required"
	case "snapshot.list":
		status, result, errMsg = e.runKopia(ctx, rep, cmd.ID, p, []string{"snapshot", "list"})
	case "snapshot.browse":
		if _, ok, parseErr := parseRepositorySpec(p.Extra["repository"]); parseErr != nil {
			status, result, errMsg = "failed", nil, parseErr.Error()
			break
		} else if ok {
			status, result, errMsg = e.runManagedSnapshotBrowse(ctx, rep, cmd.ID, p)
			break
		}
		status, result, errMsg = "failed", nil, "repository payload is required"
	case "snapshot.download":
		if _, ok, parseErr := parseRepositorySpec(p.Extra["repository"]); parseErr != nil {
			status, result, errMsg = "failed", nil, parseErr.Error()
			break
		} else if ok {
			status, result, errMsg = e.runManagedSnapshotDownload(ctx, rep, cmd.ID, p)
			break
		}
		status, result, errMsg = "failed", nil, "repository payload is required"
	case "snapshot.delete":
		if _, ok, parseErr := parseRepositorySpec(p.Extra["repository"]); parseErr != nil {
			status, result, errMsg = "failed", nil, parseErr.Error()
			break
		} else if ok {
			status, result, errMsg = e.runManagedSnapshotDelete(ctx, rep, cmd.ID, p)
			break
		}
		status, result, errMsg = "failed", nil, "repository payload is required"
	case "repository.policy.apply":
		if _, ok, parseErr := parseRepositorySpec(p.Extra["repository"]); parseErr != nil {
			status, result, errMsg = "failed", nil, parseErr.Error()
			break
		} else if ok {
			status, result, errMsg = e.runManagedPolicyApply(ctx, rep, cmd.ID, p)
			break
		}
		status, result, errMsg = "failed", nil, "repository payload is required"
	case "restore":
		if _, ok, parseErr := parseRepositorySpec(p.Extra["repository"]); parseErr != nil {
			status, result, errMsg = "failed", nil, parseErr.Error()
			break
		} else if ok {
			status, result, errMsg = e.runManagedRestore(ctx, rep, cmd.ID, p)
			break
		}
		snapshotID := p.SnapshotID
		if snapshotID == "" {
			snapshotID = "latest"
		}
		def := []string{"restore", snapshotID}
		if p.Path != "" {
			def = append(def, p.Path)
		}
		status, result, errMsg = e.runKopia(ctx, rep, cmd.ID, p, def)
	case "repo.status":
		if _, ok, parseErr := parseRepositorySpec(p.Extra["repository"]); parseErr != nil {
			status, result, errMsg = "failed", nil, parseErr.Error()
			break
		} else if ok {
			status, result, errMsg = e.runManagedRepositoryStatus(ctx, rep, cmd.ID, p)
			break
		}
		status, result, errMsg = e.runKopia(ctx, rep, cmd.ID, p, []string{"repository", "status"})
	case "repo.initialize":
		if _, ok, parseErr := parseRepositorySpec(p.Extra["repository"]); parseErr != nil {
			status, result, errMsg = "failed", nil, parseErr.Error()
			break
		} else if ok {
			status, result, errMsg = e.runManagedRepositoryInitialize(ctx, rep, cmd.ID, p)
			break
		}
		status, result, errMsg = "failed", nil, "repository payload is required"
	case "repository.server.start":
		status, result, errMsg = e.runRepositoryServerStart(ctx, rep, cmd.ID, p)
	case "repository.server.stop":
		status, result, errMsg = e.runRepositoryServerStop(ctx, p)
	case "nas.mount":
		status, result, errMsg = e.runNasMount(ctx, p)
	case "nas.unmount":
		status, result, errMsg = e.runNasUnmount(ctx, p)
	case "nas.test":
		status, result, errMsg = e.runNasTest(ctx, p)
	case "path.usage":
		status, result, errMsg = e.runPathUsage(ctx, p)
	case "path.info":
		status, result, errMsg = e.runPathInfo(ctx, p)
	case "path.size", "path.estimate", "source.path.size":
		status, result, errMsg = e.runPathSize(ctx, p)
	case "lens.ks.prepare", "lens.workspace.prepare":
		status, result, errMsg = e.runLensKsPrepare(ctx, p)
	case "lens.ks.cleanup", "lens.workspace.cleanup":
		status, result, errMsg = e.runLensKsCleanup(ctx, p)
	case "maintenance.gc":
		status, result, errMsg = e.runKopia(ctx, rep, cmd.ID, p, []string{"maintenance", "run", "--full"})
	case "repository.operation":
		status, result, errMsg = e.runManagedRepositoryOperation(ctx, rep, cmd.ID, p)
	case "agent.ping":
		status, result, errMsg = e.runPing(ctx)
	case "agent.version":
		status, result, errMsg = e.runVersion(ctx)
	case "agent.upgrade":
		status, result, errMsg = e.runAgentUpgrade(ctx, rep, cmd.ID, p)
	case "agent.uninstall":
		status, result, errMsg = e.runAgentUninstall(ctx, rep, cmd.ID, p)
	case "task.cancel", "cancel":
		status, result, errMsg = "failed", nil, "canceled"
	default:
		if len(p.Args) > 0 {
			status, result, errMsg = e.runKopia(ctx, rep, cmd.ID, p, nil)
		} else {
			status, result, errMsg = "failed", map[string]any{"kind": kind}, fmt.Sprintf("unsupported task kind %q", kind)
		}
	}

	if ctx.Err() != nil && status != "success" {
		slog.WarnContext(ctx, "engine task canceled", "node_id", nodeID, "task_id", cmd.ID, "kind", kind)
		return Result{Status: "failed", Result: result, Error: "canceled"}
	}
	if status == "success" {
		slog.InfoContext(ctx, "engine task finished", "node_id", nodeID, "task_id", cmd.ID, "kind", kind, "status", status)
	} else {
		slog.WarnContext(ctx, "engine task finished", "node_id", nodeID, "task_id", cmd.ID, "kind", kind, "status", status, "err", errMsg)
	}
	return Result{Status: status, Result: result, Error: errMsg}
}

func (e *Engine) kopiaBin(ctx context.Context) (string, error) {
	path, err := kopia.Resolve(e.current().KopiaPath)
	if err != nil {
		return "", err
	}
	if err := ctx.Err(); err != nil {
		return "", err
	}
	return path, nil
}

func (e *Engine) runBrowse(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
) (string, map[string]any, string) {
	nasSpec, hasNAS, nasParseErr := parseNASSpec(p.Extra["nas"])
	if nasParseErr != nil {
		slog.Info("browse", "event", "invalid_nas_payload", "task_id", taskID, "err", nasParseErr.Error())
		return "failed", nil, nasParseErr.Error()
	}
	if hasNAS {
		nas.LogSpec("browse_begin", nasSpec, "task_id", taskID, "path", p.Path)
	}

	if err := e.ensureNASMounted(ctx, p); err != nil {
		if hasNAS {
			nas.LogSpec("browse_mount_failed", nasSpec, "task_id", taskID, "err", err.Error())
		} else {
			slog.Info("browse", "event", "browse_mount_failed", "task_id", taskID, "err", err.Error())
		}
		return "failed", nil, err.Error()
	}

	svc := explorer.NewService()
	var listing explorer.ListResult
	var root string
	var err error
	if hasNAS && p.Path == "" {
		p.Path = nasSpec.MountPoint
	}
	if p.ListMounts || p.Path == "" {
		root = ""
		listing, err = svc.ListMountPoints(ctx, explorer.ListOptions{
			IncludeMetadata: p.IncludeMetadata,
			Limit:           p.Limit,
			Cursor:          p.Cursor,
		})
	} else {
		root = explorer.NormalizeMountPath(p.Path)
		listing, err = svc.ListLocalWithOptions(ctx, root, explorer.ListOptions{
			DirsOnly:        p.DirsOnly,
			IncludeMetadata: p.IncludeMetadata,
			Limit:           p.Limit,
			Cursor:          p.Cursor,
		})
	}
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			slog.Info("browse", "event", "browse_list_failed", "task_id", taskID, "path", root, "err", "path not found")
			return "failed", nil, "path not found"
		}
		if errors.Is(err, fs.ErrPermission) {
			slog.Info("browse", "event", "browse_list_failed", "task_id", taskID, "path", root, "err", "permission denied")
			return "failed", nil, "permission denied"
		}
		slog.Info("browse", "event", "browse_list_failed", "task_id", taskID, "path", root, "err", err.Error())
		return "failed", nil, err.Error()
	}
	slog.Info("browse", "event", "browse_list_ok", "task_id", taskID, "path", root, "count", len(listing.Entries))
	entries := listing.Entries
	rows := make([]map[string]any, 0, len(entries))
	for _, entry := range entries {
		rows = append(rows, map[string]any{
			"name":     entry.Name,
			"path":     entry.Path,
			"is_dir":   entry.IsDir,
			"size":     entry.Size,
			"mod_time": entry.ModTime,
		})
	}
	return "success", map[string]any{
		"path":        root,
		"entries":     rows,
		"count":       len(rows),
		"has_more":    listing.HasMore,
		"next_cursor": listing.NextCursor,
	}, ""
}

func (e *Engine) runKopia(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
	defaultArgs []string,
) (string, map[string]any, string) {
	bin, err := e.kopiaBin(ctx)
	if err != nil {
		return "failed", nil, err.Error()
	}

	var args []string
	switch {
	case len(p.Args) > 0:
		args = p.kopiaArgs()
	case len(defaultArgs) > 0:
		args = p.kopiaArgs(defaultArgs...)
	default:
		args = p.kopiaArgs()
	}
	if len(args) == 0 {
		return "failed", nil, "missing kopia args in payload"
	}

	runCtx, cancel := context.WithCancel(ctx)
	e.setActiveCancel(cancel)
	defer e.clearActiveCancel()

	_ = sendProgress(runCtx, rep, taskID, map[string]any{
		"phase": "kopia_start",
		"args":  args,
	})

	kopiaEnv, envErr := ensureKopiaProcessEnv(e.current(), p.Env)
	if envErr != nil {
		return "failed", nil, envErr.Error()
	}
	res, runErr := process.Run(runCtx, bin, args, kopiaEnv, "")
	progress := map[string]any{
		"phase":     "kopia_finished",
		"exit_code": res.ExitCode,
	}
	if res.Stdout != "" {
		progress["stdout_tail"] = tailLines(res.Stdout, 20)
	}
	if res.Stderr != "" {
		progress["stderr_tail"] = tailLines(res.Stderr, 20)
	}
	_ = sendProgress(runCtx, rep, taskID, progress)

	result := map[string]any{
		"exit_code": res.ExitCode,
		"stdout":    res.Stdout,
		"stderr":    res.Stderr,
	}
	if runErr != nil {
		if runCtx.Err() != nil {
			return "failed", result, "canceled"
		}
		return "failed", result, runErr.Error()
	}
	return "success", result, ""
}

func (e *Engine) runPathUsage(ctx context.Context, p Payload) (string, map[string]any, string) {
	if err := ctx.Err(); err != nil {
		return "failed", nil, "canceled"
	}
	path := strings.TrimSpace(p.Path)
	if path == "" {
		return "failed", nil, "path is required"
	}
	total, used, free, usageErr := disk.Usage(path)
	if usageErr != nil {
		return "failed", nil, usageErr.Error()
	}
	return "success", map[string]any{
		"path":        path,
		"total_bytes": total,
		"used_bytes":  used,
		"free_bytes":  free,
	}, ""
}

func (e *Engine) runPathInfo(ctx context.Context, p Payload) (string, map[string]any, string) {
	if err := ctx.Err(); err != nil {
		return "failed", nil, "canceled"
	}
	path := strings.TrimSpace(p.Path)
	if path == "" {
		return "failed", nil, "path is required"
	}
	if err := e.ensureNASMounted(ctx, p); err != nil {
		return "failed", nil, err.Error()
	}
	info, statErr := os.Stat(path)
	if statErr != nil {
		if errors.Is(statErr, fs.ErrNotExist) {
			return "failed", map[string]any{
				"path":   path,
				"exists": false,
			}, "path not found"
		}
		if errors.Is(statErr, fs.ErrPermission) {
			return "failed", map[string]any{
				"path":   path,
				"exists": false,
			}, "permission denied"
		}
		return "failed", map[string]any{
			"path":   path,
			"exists": false,
		}, statErr.Error()
	}
	isDir := info.IsDir()
	pathType := "file"
	if isDir {
		pathType = "directory"
	}
	result := map[string]any{
		"name":      info.Name(),
		"path":      path,
		"exists":    true,
		"is_dir":    isDir,
		"path_type": pathType,
	}
	if p.IncludeMetadata {
		result["size"] = info.Size()
		result["mod_time"] = info.ModTime().UTC().Format("2006-01-02T15:04:05Z07:00")
	}
	return "success", result, ""
}

func (e *Engine) runPathSize(ctx context.Context, p Payload) (string, map[string]any, string) {
	if err := ctx.Err(); err != nil {
		return "failed", nil, "canceled"
	}
	path := strings.TrimSpace(p.Path)
	if path == "" {
		return "failed", nil, "path is required"
	}
	if err := e.ensureNASMounted(ctx, p); err != nil {
		return "failed", nil, err.Error()
	}
	pathType := payloadStringValue(p.Extra["path_type"])
	if pathType == "" {
		pathType = "directory"
	}
	sizeBytes, sizeErr := pathsize.Estimate(path, pathType)
	if sizeErr != nil {
		if errors.Is(sizeErr, fs.ErrNotExist) {
			return "failed", map[string]any{"path": path, "exists": false}, "path not found"
		}
		if errors.Is(sizeErr, fs.ErrPermission) {
			return "failed", map[string]any{"path": path, "exists": false}, "permission denied"
		}
		return "failed", map[string]any{"path": path}, sizeErr.Error()
	}
	return "success", map[string]any{
		"path":       path,
		"path_type":  pathType,
		"size_bytes": sizeBytes,
	}, ""
}

func (e *Engine) runPing(ctx context.Context) (string, map[string]any, string) {
	if err := ctx.Err(); err != nil {
		return "failed", nil, "canceled"
	}
	return "success", map[string]any{
		"pong": true,
		"role": string(e.current().Role),
	}, ""
}

func (e *Engine) runVersion(ctx context.Context) (string, map[string]any, string) {
	out := map[string]any{
		"agent_version": selfupdate.Version,
		"agent_commit":  selfupdate.Commit,
		"role":          string(e.current().Role),
	}
	bin, err := e.kopiaBin(ctx)
	if err != nil {
		out["kopia_error"] = err.Error()
		return "success", out, ""
	}
	ver, verErr := kopia.Version(ctx, bin)
	if verErr != nil {
		out["kopia_error"] = verErr.Error()
	} else {
		out["kopia_version"] = ver
		out["kopia_path"] = bin
	}
	return "success", out, ""
}

func (e *Engine) setActiveCancel(cancel context.CancelFunc) {
	e.activeMu.Lock()
	defer e.activeMu.Unlock()
	if e.activeKill != nil {
		e.activeKill()
	}
	e.activeKill = cancel
}

func (e *Engine) clearActiveCancel() {
	e.activeMu.Lock()
	defer e.activeMu.Unlock()
	e.activeKill = nil
}

// Cancel stops the in-flight subprocess started by this engine.
func (e *Engine) Cancel() {
	e.activeMu.Lock()
	cancel := e.activeKill
	e.activeMu.Unlock()
	if cancel != nil {
		cancel()
	}
}

func sendProgress(ctx context.Context, rep ReporterSink, taskID string, progress map[string]any) error {
	if rep.Sink == nil || taskID == "" {
		return nil
	}
	return rep.TaskProgress(ctx, taskID, progress)
}

func tailLines(s string, max int) string {
	lines := strings.Split(strings.TrimSpace(s), "\n")
	if len(lines) <= max {
		return s
	}
	return strings.Join(lines[len(lines)-max:], "\n")
}
