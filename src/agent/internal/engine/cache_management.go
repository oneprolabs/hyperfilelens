package engine

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"

	"hyperfilelens/agent/internal/infra/kopialayout"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/kopia"
	"hyperfilelens/agent/internal/platform/vfs"
)

func (e *Engine) cacheManagementSupport(ctx context.Context) (bool, error) {
	if role := e.current().Role; role != model.RoleAgent && role != model.RoleProxy {
		return false, nil
	}
	bin, err := e.kopiaBin(ctx)
	if err != nil {
		return false, err
	}
	return kopialayout.ProbeDemandDriven(ctx, bin)
}

var errCacheBinaryIncompatible = errors.New("the installed Kopia does not support the applied cache policy; restore a compatible Agent/Kopia package")

type cacheExecutionMode struct {
	enabled     bool
	applyPolicy bool
}

// Capability evidence authorizes enabling/changing policy. A persisted opt-in
// independently requires retaining demand-driven mode and backup protection
// when a transient help probe fails.
func (e *Engine) resolveCacheExecutionMode(ctx context.Context) (cacheExecutionMode, error) {
	if role := e.current().Role; role != model.RoleAgent && role != model.RoleProxy {
		return cacheExecutionMode{}, nil
	}
	_, applied, err := kopialayout.ReadAppliedPolicy(agentDataDir(e.current()))
	if err != nil {
		return cacheExecutionMode{enabled: true}, err
	}
	supported, probeErr := e.cacheManagementSupport(ctx)
	if err := ctx.Err(); err != nil {
		return cacheExecutionMode{}, err
	}
	if probeErr != nil {
		slog.WarnContext(ctx, "kopia_cache_capability_check_deferred", "preserving_applied_policy", applied)
		return cacheExecutionMode{enabled: applied}, nil
	}
	if applied && !supported {
		return cacheExecutionMode{}, errCacheBinaryIncompatible
	}
	return cacheExecutionMode{enabled: supported, applyPolicy: supported}, nil
}

func (e *Engine) applyCachePayload(ctx context.Context, p Payload) error {
	root := agentDataDir(e.current())
	policy, err := kopialayout.ReadPolicy(root)
	if err != nil {
		return err
	}
	current := policy
	if raw, ok := p.Extra["cache_policy"]; ok {
		data, err := json.Marshal(raw)
		if err != nil {
			return err
		}
		if err = json.Unmarshal(data, &policy); err != nil {
			return err
		}
	}
	if err := policy.Validate(); err != nil {
		return err
	}
	if policy.Revision < current.Revision {
		return nil
	} // Redelivered work must use the newer locally applied policy.
	return kopialayout.ApplyPolicy(ctx, root, policy)
}
func (e *Engine) cacheVerifier(ctx context.Context) func(string) error {
	root := agentDataDir(e.current())
	return func(config string) error {
		return kopialayout.VerifyCacheConnection(ctx, root, config)
	}
}
func (e *Engine) runCacheManagement(ctx context.Context, cmd Command, p Payload, sink ExecutionSink) Result {
	root := agentDataDir(e.current())
	supported, probeErr := e.cacheManagementSupport(ctx)
	if probeErr != nil {
		return Result{Status: "failed", Error: probeErr.Error()}
	}
	if !supported {
		return Result{Status: "failed", Error: "CACHE_MANAGEMENT_UNSUPPORTED: upgrade the Agent/Proxy and bundled Kopia"}
	}
	logs := e.current().LogDir
	if logs == "" {
		logs = vfs.AgentLogDir(root)
	}
	if cmd.Kind != "cache.relocate" && cmd.Kind != "cache.relocation.retry" {
		release, err := kopialayout.AcquireUsage(ctx, root)
		if err != nil {
			return Result{Status: "failed", Error: err.Error()}
		}
		defer release()
		if err := e.applyCachePayload(ctx, p); err != nil {
			return Result{Status: "failed", Error: err.Error()}
		}
	}
	var data map[string]any
	var err error
	switch cmd.Kind {
	case "cache.policy.apply":
		err = e.applyCachePayload(ctx, p)
		if err == nil {
			data = e.cacheStatus(ctx, true)
		}
	case "cache.status":
		data = e.cacheStatus(ctx, true)
	case "cache.relocation.validate":
		parent, _ := p.Extra["parent"].(string)
		var validation kopialayout.Validation
		validation, err = kopialayout.ValidateDestination(ctx, root, parent, logs)
		if err == nil {
			raw, _ := json.Marshal(validation)
			_ = json.Unmarshal(raw, &data)
		}
	case "cache.relocation.retry":
		previous, _ := p.Extra["previous_task_id"].(string)
		data, err = kopialayout.RetryCleanup(ctx, root, logs, cmd.ID, previous, func(state map[string]any) {
			if sink != nil {
				_ = sink.OnProgress(ctx, map[string]any{"phase": state["stage"], "relocation": state})
			}
		}, e.cacheVerifier(ctx))
	case "cache.relocate":
		token, _ := p.Extra["validation_token"].(string)
		data, err = kopialayout.Relocate(ctx, root, logs, cmd.ID, token, func(state map[string]any) {
			if sink != nil {
				_ = sink.OnProgress(ctx, map[string]any{"phase": state["stage"], "relocation": state})
			}
		}, e.cacheVerifier(ctx))
	default:
		err = fmt.Errorf("unknown cache command")
	}
	if err != nil {
		return Result{Status: "failed", Result: data, Error: err.Error()}
	}
	return Result{Status: "success", Result: data}
}
func (e *Engine) cacheStatus(ctx context.Context, usage bool) map[string]any {
	root := agentDataDir(e.current())
	policy, err := kopialayout.ReadPolicy(root)
	data := map[string]any{"policy": policy, "application_state": "applied", "mode": "demand_driven", "active_cache_root": kopialayout.ActiveCacheRoot(root), "relocation": kopialayout.RelocationStatus(root)}
	if _, activeErr := kopialayout.ReadActiveRoot(root); activeErr != nil {
		data["application_state"] = "failed"
		data["active_cache_root"] = ""
		data["error"] = "Active cache filesystem is unavailable."
	}
	if err != nil {
		data["application_state"] = "failed"
		data["error"] = "Cache policy could not be read."
	}
	if usage {
		data["usage"] = kopialayout.CacheUsage(ctx, root)
		check, e := kopialayout.CheckSpace(root, nil)
		if e == nil {
			data["space"] = check
		} else {
			data["space_error"] = "Cache space could not be determined."
		}
	}
	return data
}
func backupCacheUse(kind string, p Payload) bool {
	return kind == "backup" || kind == "backup.snapshot.create" || p.Extra["cache_use_context"] == "backup"
}

// Recheck persisted opt-in at each backup checkpoint. A policy can be enabled
// after admission; protection must not depend on an earlier capability snapshot.
func (e *Engine) backupCacheCheck(ctx context.Context, p Payload, sink ExecutionSink, enabled bool) (map[string]any, error) {
	if role := e.current().Role; role != model.RoleAgent && role != model.RoleProxy {
		return nil, nil
	}
	if !enabled {
		_, applied, err := kopialayout.ReadAppliedPolicy(agentDataDir(e.current()))
		if !applied && err == nil {
			return nil, nil
		}
	}
	return e.backupCachePreflight(ctx, p, sink)
}

func (e *Engine) backupCachePreflight(ctx context.Context, p Payload, sink ExecutionSink) (map[string]any, error) {
	root := agentDataDir(e.current())
	check, err := kopialayout.CheckSpace(root, nil)
	data := map[string]any{"cache_space_check": check}
	if sink != nil {
		_ = sink.OnProgress(ctx, map[string]any{"phase": "cache_space_preflight", "cache_space_check": check})
	}
	if err != nil {
		data["error_code"] = "CACHE_SPACE_CHECK_FAILED"
		return data, fmt.Errorf("CACHE_SPACE_CHECK_FAILED: cannot query available space for cache %q; check mount and executing-account permissions", check.Path)
	}
	if check.State == "blocked" {
		data["error_code"] = "CACHE_SPACE_INSUFFICIENT"
		return data, fmt.Errorf("CACHE_SPACE_INSUFFICIENT: cache %q has %d available bytes; backup requires %d bytes (warning %d); free space, expand the volume or safely relocate cache, then run a new backup", check.Path, check.Available, check.Policy.Blocking, check.Policy.Warning)
	}
	return data, nil
}

// CacheInventory is a bounded projection. Usage is measured by explicit status
// tasks, never by recurring full filesystem walks in heartbeat generation.
func CacheInventory(ctx context.Context, root, bin string) (map[string]any, bool) {
	policy, applied, policyErr := kopialayout.ReadAppliedPolicy(root)
	var supported bool
	var probeErr error
	if resolved, err := kopia.Resolve(bin); err == nil {
		supported, probeErr = kopialayout.ProbeDemandDriven(ctx, resolved)
	} else {
		probeErr = err
	}
	if !supported && !applied && policyErr == nil {
		return nil, false
	}
	state := "applied"
	if !applied {
		state = "pending"
	}
	if policyErr != nil || probeErr != nil || !supported {
		state = "failed"
	}
	if _, activeErr := kopialayout.ReadActiveRoot(root); activeErr != nil {
		state = "failed"
	}
	data := map[string]any{"policy": policy, "application_state": state, "mode": "demand_driven", "active_cache_root": kopialayout.ActiveCacheRoot(root), "relocation": kopialayout.RelocationStatus(root)}
	if policyErr != nil {
		// Invalid bytes are not an authoritative new policy for the server.
		data["policy"] = nil
	}
	return data, supported
}

// Used by startup recovery without exposing connection material.
func RecoverCacheRelocation(ctx context.Context, cfgRoot, logs string) error {
	return kopialayout.RecoverRelocation(ctx, cfgRoot, logs, func(config string) error {
		return kopialayout.VerifyCacheConnection(ctx, cfgRoot, config)
	})
}
func (e *Engine) validateCacheRestoreTarget(path string) error {
	a, err := kopialayout.ReadActiveRoot(agentDataDir(e.current()))
	if err != nil {
		return err
	}
	target, err := canonicalPath(path)
	if err != nil {
		return err
	}
	cache, err := canonicalPath(a.Path)
	if err != nil {
		return err
	}
	if isWithin(target, cache) || isWithin(cache, target) {
		return fmt.Errorf("restore target overlaps managed cache; choose a separate business-data directory")
	}
	return nil
}

type backupSpaceCheckKey struct{}
