package engine

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"

	"hyperfilelens/agent/internal/platform/process"
)

var orphanSnapshotID = regexp.MustCompile(`^[0-9a-f]{32}$`)

func parseOrphanManifestInventory(raw string) ([]map[string]any, error) {
	var rows []map[string]any
	if err := json.Unmarshal([]byte(raw), &rows); err != nil || rows == nil {
		return nil, fmt.Errorf("incomplete snapshot manifest inventory")
	}
	seen := map[string]bool{}
	for _, row := range rows {
		id, _ := row["id"].(string)
		labels, ok := row["labels"].(map[string]any)
		if !orphanSnapshotID.MatchString(id) || seen[id] || !ok || labels["type"] != "snapshot" {
			return nil, fmt.Errorf("invalid snapshot manifest identity")
		}
		for _, value := range labels {
			if _, ok := value.(string); !ok {
				return nil, fmt.Errorf("invalid snapshot manifest labels")
			}
		}
		seen[id] = true
	}
	return rows, nil
}

func (e *Engine) runOrphanSnapshotOperation(
	ctx context.Context, rep ReporterSink, taskID string, p Payload,
) (string, map[string]any, string) {
	operation := payloadStringValue(p.Extra["operation_type"])
	if operation != "snapshot.inventory" && operation != "snapshot.orphan_delete" {
		return "failed", nil, "unsupported orphan snapshot operation"
	}
	config, env, result, spec, prepErr := e.prepareManagedRepository(ctx, rep, taskID, p, repositoryPrepareConnect)
	if prepErr != "" {
		return "failed", result, prepErr
	}
	release, err := repositorySessionLockFor(spec, config).acquireWrite(ctx)
	if err != nil {
		return "failed", result, err.Error()
	}
	defer release()
	bin, err := e.kopiaBin(ctx)
	if err != nil {
		return "failed", result, err.Error()
	}
	args := []string{"--config-file=" + config, "manifest", "list", "--filter=type:snapshot", "--json"}
	listed, err := process.Run(ctx, bin, args, env, "")
	if err != nil {
		return "failed", result, "unable to list complete snapshot inventory"
	}
	rows, err := parseOrphanManifestInventory(listed.Stdout)
	if err != nil {
		return "failed", result, err.Error()
	}
	if operation == "snapshot.inventory" {
		result["manifests"] = rows
		result["inventory_complete"] = true
		return "success", result, ""
	}
	rawIDs, ok := p.Extra["snapshot_ids"].([]any)
	if !ok || len(rawIDs) != 1 {
		return "failed", result, "one exact snapshot identity is required"
	}
	id, ok := rawIDs[0].(string)
	if !ok || !orphanSnapshotID.MatchString(id) {
		return "failed", result, "invalid snapshot identity"
	}
	found := false
	result["snapshot_id"] = id
	for _, row := range rows {
		if row["id"] == id {
			found = true
		}
	}
	if !found {
		result["deleted"] = false
		return "success", result, ""
	}
	deleted, err := process.Run(ctx, bin, managedSnapshotDeleteArgs(config, id), env, "")
	if err != nil || deleted.ExitCode != 0 {
		if ctx.Err() == nil {
			result["execution_complete"] = true
		}
		return "failed", result, "snapshot deletion failed"
	}
	result["deleted"] = true
	result["snapshot_id"] = id
	return "success", result, ""
}
