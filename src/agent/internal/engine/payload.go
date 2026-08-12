package engine

import (
	"fmt"
	"math"
	"strings"
)

// Payload is the task.command JSON body from the control plane or CLI.
type Payload struct {
	Path            string
	ConfigFile      string
	SnapshotID      string
	Args            []string
	Env             map[string]string
	DirsOnly        bool
	IncludeMetadata bool
	Limit           int
	Cursor          string
	ListMounts      bool
	Extra           map[string]any
}

func ParsePayload(raw map[string]any) Payload {
	if raw == nil {
		return Payload{IncludeMetadata: true}
	}
	p := Payload{Extra: raw, IncludeMetadata: true}
	if v, ok := raw["path"].(string); ok {
		p.Path = strings.TrimSpace(v)
	}
	if v, ok := raw["config_file"].(string); ok {
		p.ConfigFile = strings.TrimSpace(v)
	}
	if v, ok := raw["snapshot_id"].(string); ok {
		p.SnapshotID = strings.TrimSpace(v)
	}
	if v, ok := raw["snapshot"].(string); ok && p.SnapshotID == "" {
		p.SnapshotID = strings.TrimSpace(v)
	}
	if v, ok := raw["kopia_snapshot_id"].(string); ok && p.SnapshotID == "" {
		p.SnapshotID = strings.TrimSpace(v)
	}
	if v, ok := raw["source_path"].(string); ok && p.Path == "" {
		p.Path = strings.TrimSpace(v)
	}
	if args, ok := raw["args"].([]any); ok {
		for _, item := range args {
			if s, ok := item.(string); ok {
				p.Args = append(p.Args, s)
			}
		}
	}
	if env, ok := raw["env"].(map[string]any); ok {
		p.Env = stringMap(env)
	}
	if v, ok := payloadBoolValue(raw["dirs_only"]); ok {
		p.DirsOnly = v
	}
	if v, ok := payloadBoolValue(raw["include_metadata"]); ok {
		p.IncludeMetadata = v
	}
	if v, ok := payloadIntValue(raw["limit"]); ok && v > 0 {
		p.Limit = v
	}
	if v, ok := raw["cursor"].(string); ok {
		p.Cursor = strings.TrimSpace(v)
	}
	if v, ok := payloadBoolValue(raw["list_mounts"]); ok {
		p.ListMounts = v
	}
	return p
}

func (p Payload) kopiaArgs(subcmd ...string) []string {
	args := make([]string, 0, len(subcmd)+2+len(p.Args))
	if p.ConfigFile != "" {
		args = append(args, "--config-file="+p.ConfigFile)
	}
	args = append(args, subcmd...)
	if len(p.Args) > 0 {
		args = append(args, p.Args...)
	}
	return args
}

func stringMap(m map[string]any) map[string]string {
	out := make(map[string]string, len(m))
	for k, v := range m {
		out[k] = fmt.Sprint(v)
	}
	return out
}

func payloadBoolValue(v any) (bool, bool) {
	switch x := v.(type) {
	case bool:
		return x, true
	case string:
		s := strings.ToLower(strings.TrimSpace(x))
		switch s {
		case "1", "true", "yes", "y", "on":
			return true, true
		case "0", "false", "no", "n", "off":
			return false, true
		}
	}
	return false, false
}

func payloadIntValue(v any) (int, bool) {
	switch x := v.(type) {
	case int:
		return x, true
	case int64:
		return int(x), true
	case float64:
		if math.IsNaN(x) || math.IsInf(x, 0) || math.Trunc(x) != x {
			return 0, false
		}
		maxInt := int64(^uint(0) >> 1)
		minInt := -maxInt - 1
		if x < float64(minInt) || x > float64(maxInt) {
			return 0, false
		}
		return int(x), true
	case string:
		s := strings.TrimSpace(x)
		if s == "" {
			return 0, false
		}
		var out int
		var trailing string
		if count, err := fmt.Sscanf(s, "%d%s", &out, &trailing); err == nil && count == 1 {
			return out, true
		}
	}
	return 0, false
}

func payloadStringValue(v any) string {
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return strings.TrimSpace(s)
	}
	return strings.TrimSpace(fmt.Sprint(v))
}

// NormalizeKind maps aliases to canonical task kinds.
func NormalizeKind(kind string) string {
	k := strings.ToLower(strings.TrimSpace(kind))
	switch k {
	case "explorer.list", "list.local", "list_local", "dir.list", "directory.list", "fs.ls":
		return "browse"
	case "backup.run", "snapshot.create", "kopia.snapshot":
		return "backup"
	case "backup.snapshot.create", "prepared.snapshot.create":
		return "backup.snapshot.create"
	case "snapshot.list", "kopia.snapshot.list":
		return "snapshot.list"
	case "snapshot.browse", "kopia.snapshot.browse":
		return "snapshot.browse"
	case "lens.snapshot.browse":
		return "lens.snapshot.browse"
	case "lens.snapshot.scope.resolve":
		return "lens.snapshot.scope.resolve"
	case "snapshot.download", "kopia.snapshot.download":
		return "snapshot.download"
	case "snapshot.delete", "snapshot.remove", "kopia.snapshot.delete", "kopia.snapshot.remove":
		return "snapshot.delete"
	case "repository.policy.apply", "repo.policy.apply", "kopia.policy.apply":
		return "repository.policy.apply"
	case "restore.run", "kopia.restore":
		return "restore"
	case "repo.status", "repository.status":
		return "repo.status"
	case "repo.initialize", "repository.initialize":
		return "repo.initialize"
	case "repository.server.start", "repo.server.start", "kopia.server.start":
		return "repository.server.start"
	case "repository.server.stop", "repo.server.stop", "kopia.server.stop":
		return "repository.server.stop"
	case "path.info", "path.stat", "fs.stat", "source.path.info":
		return "path.info"
	case "path.size", "path.estimate", "source.path.size", "fs.du":
		return "path.size"
	case "path.usage", "disk.usage":
		return "path.usage"
	case "maintenance.run", "maintenance.run_gc", "kopia.maintenance", "gc":
		return "maintenance.gc"
	case "repository.operation", "repository.maintenance":
		return "repository.operation"
	case "health", "agent.ping":
		return "agent.ping"
	case "agent.info":
		return "agent.version"
	case "agent.upgrade", "upgrade.agent":
		return "agent.upgrade"
	case "agent.uninstall", "uninstall.agent":
		return "agent.uninstall"
	case "source.mount", "storage.mount":
		return "nas.mount"
	case "source.unmount", "storage.unmount":
		return "nas.unmount"
	case "source.test", "connection.test", "nas.connection.test":
		return "nas.test"
	case "lens.workspace.prepare":
		return "lens.ks.prepare"
	case "lens.workspace.cleanup":
		return "lens.ks.cleanup"
	case "lens.gateway.browse", "lens.workspace.validate-local":
		return k
	default:
		return k
	}
}
