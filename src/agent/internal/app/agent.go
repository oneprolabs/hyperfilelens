package app

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"math/rand"
	"os"
	"strings"
	"sync"
	"time"

	"hyperfilelens/agent/internal/controller"
	"hyperfilelens/agent/internal/enroll"
	"hyperfilelens/agent/internal/infra/config"
	"hyperfilelens/agent/internal/infra/database"
	"hyperfilelens/agent/internal/infra/monitor"
	"hyperfilelens/agent/internal/infra/observability"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/remote"
	"hyperfilelens/agent/internal/selfupdate"
	"hyperfilelens/agent/internal/wire"
)

const controlPlanePollInterval = 5 * time.Second
const gatewayObservabilityRefreshInterval = 10 * time.Minute
const heartbeatCollectionInterval = 30 * time.Second

// Agent is the runtime composition root coordinating module startup and shutdown.
type Agent struct {
	store     *config.Store
	db        *database.DB
	connector *remote.Connector
	wire      *wire.Handler
	sender    *remote.Sender
	scheduler *controller.Scheduler
	tracker   *controller.Tracker
	taskFixer *controller.TaskFixer
	monitor   *monitor.Collector

	heartbeatMu      sync.RWMutex
	storageInventory map[string]any
	monitorMetrics   map[string]any

	idleLogged bool
}

// New creates an Agent instance backed by a hot-reloadable config Store.
func New(store *config.Store) *Agent {
	snapshotConcurrency := 2
	if store != nil && store.Current().BackupSnapshotConcurrency > 0 {
		snapshotConcurrency = store.Current().BackupSnapshotConcurrency
	}
	slog.Info("backup snapshot scheduler configured", "max_concurrent", snapshotConcurrency)
	return &Agent{
		store:            store,
		sender:           remote.NewSender(),
		scheduler:        controller.NewScheduler(snapshotConcurrency),
		tracker:          controller.NewTracker(),
		monitor:          monitor.NewCollector(),
		storageInventory: remote.EmptyStorageInventoryPayload(),
	}
}

// Startup performs environment checks, opens local DB, repairs stale tasks, and starts subsystems.
func (a *Agent) Startup(ctx context.Context) error {
	cfg := a.store.Current()
	if err := Setup(ctx, cfg); err != nil {
		return err
	}

	dataRoot, logDir, _, err := ResolveLayout(cfg)
	if err != nil {
		return err
	}
	db, err := database.Open(ctx, database.DefaultPath(dataRoot))
	if err != nil {
		return fmt.Errorf("open task database: %w", err)
	}
	a.db = db
	slog.Info("task database ready", "path", db.Path())

	repo := database.NewTaskRepo(db)
	a.wire = wire.NewHandler(a.store, a.tracker, repo, a.scheduler)
	a.taskFixer = controller.NewTaskFixer(repo, a.tracker, dataRoot, logDir)

	if _, err := a.taskFixer.RepairRunning(ctx); err != nil {
		return err
	}

	wss := strings.TrimSpace(cfg.WSSURL)
	slog.Info("agent starting",
		"version", selfupdate.Version,
		"commit", selfupdate.Commit,
		"role", cfg.Role,
		"wss_configured", wss != "",
	)
	if wss == "" {
		slog.Info("control plane idle: set HFL_WSS_URL in agent.env or `hfl-agent config set` to connect")
		a.idleLogged = true
	}
	envPath, jsonPath := a.store.Paths()
	slog.Info("config files", "env_file", envPath, "json_file", jsonPath)

	return nil
}

// Run blocks until ctx is cancelled.
func (a *Agent) Run(ctx context.Context) error {
	if err := a.Startup(ctx); err != nil {
		return err
	}
	defer a.Shutdown(context.Background())
	go a.storageInventoryLoop(ctx)
	go a.networkStorageInventoryLoop(ctx)
	go a.monitorCollectionLoop(ctx)

	for {
		if err := ctx.Err(); err != nil {
			return err
		}

		wss := strings.TrimSpace(a.store.Current().WSSURL)
		if wss == "" {
			a.waitControlPlaneConfig(ctx)
			continue
		}
		a.idleLogged = false

		if a.connector == nil {
			a.connector = remote.NewConnector(a.store)
			a.sender.Bind(a.connector)
			a.connector.SetHeartbeatHook(func(context.Context) map[string]any {
				return a.heartbeatPayload()
			})
		}

		if err := remote.EnsureNodeRegistered(ctx, a.store, a.store); err != nil {
			slog.Warn("node registration before websocket failed", "err", err)
		}

		err := a.connector.Run(ctx,
			func(ctx context.Context, msg []byte) error {
				return a.wire.Handle(ctx, msg, a.connector)
			},
			func(ctx context.Context) error {
				a.wire.SetTaskResultAckEnabled(a.connector.TaskResultAckEnabled())
				if a.taskFixer != nil {
					if _, err := a.taskFixer.RepairRunning(ctx); err != nil {
						slog.Warn("connect lifecycle repair failed", "err", err)
					}
				}
				if err := a.wire.ReattachRunningTasks(ctx, a.connector); err != nil {
					slog.Warn("reattach running tasks failed", "err", err)
				}
				if err := a.wire.FlushUnreportedResults(ctx, a.connector); err != nil {
					return err
				}
				if a.wire.TaskResultAckEnabled() {
					go a.taskResultOutboxLoop(ctx)
				}
				if err := remote.SendInventory(
					ctx,
					a.connector,
					a.store,
					a.storageInventorySnapshot(),
				); err != nil {
					return err
				}
				go a.deferredLifecycleRepair(context.WithoutCancel(ctx))
				if a.store.Current().Role == model.RoleGateway {
					go a.gatewayObservabilityLoop(ctx)
				}
				return nil
			},
		)
		if errors.Is(err, context.Canceled) {
			return err
		}
		if err != nil {
			slog.Warn("websocket session ended", "err", err)
		}

		if strings.TrimSpace(a.store.Current().WSSURL) == "" {
			a.connector = nil
		}
	}
}

func (a *Agent) heartbeatPayload() map[string]any {
	a.heartbeatMu.RLock()
	defer a.heartbeatMu.RUnlock()
	payload := clonePayload(a.storageInventory)
	if len(a.monitorMetrics) > 0 {
		payload["metrics"] = a.monitorMetrics
	}
	return payload
}

func (a *Agent) storageInventorySnapshot() map[string]any {
	a.heartbeatMu.RLock()
	defer a.heartbeatMu.RUnlock()
	return clonePayload(a.storageInventory)
}

func clonePayload(source map[string]any) map[string]any {
	result := make(map[string]any, len(source))
	for key, value := range source {
		result[key] = value
	}
	return result
}

func (a *Agent) storageInventoryLoop(ctx context.Context) {
	a.collectStorageInventory()
	ticker := time.NewTicker(heartbeatCollectionInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			a.collectStorageInventory()
		}
	}
}

func (a *Agent) collectStorageInventory() {
	payload, err := remote.CollectStorageInventoryPayload()
	if err != nil {
		slog.Debug("storage inventory collection failed", "err", err)
		return
	}
	a.heartbeatMu.Lock()
	if current, ok := a.storageInventory["network_storage_pools"]; ok {
		payload["network_storage_pools"] = current
	}
	if current, ok := a.storageInventory["network_storage_inventory_status"]; ok {
		payload["network_storage_inventory_status"] = current
	}
	a.storageInventory = payload
	a.heartbeatMu.Unlock()
}

func (a *Agent) networkStorageInventoryLoop(ctx context.Context) {
	a.collectNetworkStorageInventory()
	ticker := time.NewTicker(heartbeatCollectionInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			a.collectNetworkStorageInventory()
		}
	}
}

func (a *Agent) collectNetworkStorageInventory() {
	pools, err := remote.CollectNetworkStorageInventory()
	if err != nil {
		slog.Debug("network storage inventory collection failed", "err", err)
		return
	}
	a.heartbeatMu.Lock()
	if a.storageInventory == nil {
		a.storageInventory = remote.EmptyStorageInventoryPayload()
	}
	a.storageInventory["network_storage_pools"] = pools
	a.storageInventory["network_storage_inventory_status"] = "ready"
	a.heartbeatMu.Unlock()
}

func (a *Agent) monitorCollectionLoop(ctx context.Context) {
	a.collectMonitorSample(ctx)
	ticker := time.NewTicker(heartbeatCollectionInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			a.collectMonitorSample(ctx)
		}
	}
}

func (a *Agent) collectMonitorSample(ctx context.Context) {
	sample, err := a.monitor.SampleOnce(ctx)
	if err != nil {
		slog.Debug("monitor sample failed", "err", err)
		return
	}
	a.heartbeatMu.Lock()
	a.monitorMetrics = sample.ToPayload()
	a.heartbeatMu.Unlock()
}

func (a *Agent) gatewayObservabilityLoop(ctx context.Context) {
	agentEnvPath, _ := a.store.Paths()
	refresh := func() {
		current := a.store.Current()
		if current.Role != model.RoleGateway {
			if err := observability.Configure(observability.Policy{}); err != nil {
				slog.Warn("gateway Agent Sentry disable failed; continuing", "err", err)
			}
			return
		}
		cfg := enroll.Config{
			OrgKey:      current.OrgKey,
			NodeRole:    current.Role,
			NodeToken:   current.NodeToken,
			APIBase:     current.APIBaseURL,
			WSSURL:      current.WSSURL,
			InsecureTLS: strings.TrimSpace(strings.ToLower(os.Getenv("HFL_INSECURE_TLS"))) != "0",
		}
		nodeID := strings.TrimSpace(current.NodeID)
		if nodeID == "" {
			nodeID = strings.TrimSpace(enroll.ReadNodeID(agentEnvPath))
		}
		lens, err := enroll.FetchGatewayLensConfig(ctx, cfg, nodeID)
		if err != nil {
			slog.Warn("gateway observability refresh failed; preserving current policy", "err", err)
			return
		}
		agentChanged, persistErr := enroll.SyncManagedObservabilityPolicyAt(
			agentEnvPath,
			lens.Observability,
		)
		if persistErr != nil {
			slog.Warn("gateway Agent observability persistence failed; continuing", "err", persistErr)
		}
		configureErr := observability.Configure(observability.Policy{
			Enabled:          lens.Observability.Enabled,
			BackendDSN:       lens.Observability.BackendDSN,
			Environment:      lens.Observability.Environment,
			Release:          lens.Observability.AgentRelease,
			TracesSampleRate: lens.Observability.TracesSampleRate,
		})
		if configureErr != nil {
			slog.Warn("gateway Agent Sentry reconfiguration failed; continuing", "err", configureErr)
		}
		lensChanged, lensErr := enroll.ConvergeGatewayLensObservability(ctx, cfg, lens)
		if lensErr != nil {
			slog.Warn("gateway LensNode observability convergence failed; continuing", "err", lensErr)
		}
		if persistErr == nil && configureErr == nil && lensErr == nil && (agentChanged || lensChanged) {
			slog.Info("gateway observability policy converged", "enabled", lens.Observability.Enabled)
		}
	}

	refresh()
	ticker := time.NewTicker(gatewayObservabilityRefreshInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			refresh()
		}
	}
}

func taskResultOutboxRetryDelay(attempt int) time.Duration {
	bases := []time.Duration{time.Second, 4 * time.Second, 16 * time.Second, 30 * time.Second}
	base := 60 * time.Second
	if attempt >= 0 && attempt < len(bases) {
		base = bases[attempt]
	}
	jitter := time.Duration(rand.Int63n(int64(base/5 + 1)))
	if rand.Intn(2) == 0 {
		return base - jitter
	}
	return base + jitter
}

func (a *Agent) taskResultOutboxLoop(ctx context.Context) {
	for attempt := 0; ; attempt++ {
		timer := time.NewTimer(taskResultOutboxRetryDelay(attempt))
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
		}
		if a.wire == nil || a.connector == nil || !a.wire.TaskResultAckEnabled() {
			return
		}
		if err := a.wire.FlushUnreportedResults(ctx, a.connector); err != nil {
			slog.Warn("task.result outbox retry failed", "attempt", attempt+1, "err", err)
		}
	}
}

func (a *Agent) waitControlPlaneConfig(ctx context.Context) {
	if !a.idleLogged {
		slog.Info("control plane idle: HFL_WSS_URL not set; waiting for configuration")
		a.idleLogged = true
	}
	select {
	case <-ctx.Done():
	case <-time.After(controlPlanePollInterval):
	}
}

// deferredLifecycleRepair re-checks detached upgrade/uninstall tasks after reconnect.
// Startup repair can run while install.ps1 is still executing; this flushes success once logs exist.
func (a *Agent) deferredLifecycleRepair(ctx context.Context) {
	delays := []time.Duration{10 * time.Second, 25 * time.Second}
	for _, delay := range delays {
		select {
		case <-ctx.Done():
			return
		case <-time.After(delay):
		}
		if a.taskFixer == nil || a.wire == nil || a.connector == nil {
			return
		}
		repaired, err := a.taskFixer.RepairRunning(ctx)
		if err != nil {
			slog.Warn("deferred lifecycle repair failed", "err", err)
			continue
		}
		if len(repaired) == 0 {
			continue
		}
		if err := a.wire.FlushUnreportedResults(ctx, a.connector); err != nil {
			slog.Warn("flush deferred lifecycle repair failed", "err", err)
		}
	}
}

// Shutdown stops active tasks and releases resources.
func (a *Agent) Shutdown(ctx context.Context) {
	_ = ctx
	for _, task := range a.tracker.Active() {
		_ = a.tracker.Cancel(ctx, task.ID)
	}
	if a.db != nil {
		_ = a.db.Close()
		a.db = nil
	}
}
