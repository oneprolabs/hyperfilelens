// Package performance owns optional, fail-open Agent governance. It must never
// return an error to a backup/restore execution path.
package performance

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	gprocess "github.com/shirou/gopsutil/v4/process"
	"hyperfilelens/agent/internal/infra/logging"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/atomicfile"
	"hyperfilelens/agent/internal/platform/tlsclient"
)

type Settings struct {
	CPU       int    `json:"cpuCores"`
	Memory    int    `json:"memCapMb"`
	Bandwidth int    `json:"bandwidth"`
	Cache     *int   `json:"kopiaCacheMb,omitempty"`
	Level     string `json:"log"`
	Retention int    `json:"logRetention"`
	LogSpace  int    `json:"logSpaceMb"`
}
type Policy struct {
	Revision int64    `json:"revision"`
	Settings Settings `json:"settings"`
}
type cachedPolicy struct {
	NodeID         string `json:"node_id"`
	OrgKeyHash     string `json:"org_key_hash"`
	InstallationID string `json:"installation_id"`
	Origin         string `json:"origin"`
	Policy         Policy `json:"policy"`
}
type Item struct {
	State  string `json:"state"`
	Reason string `json:"reason"`
}
type Report struct {
	Revision         int64           `json:"revision"`
	Items            map[string]Item `json:"items"`
	ObservedRevision int64           `json:"observed_revision,omitempty"`
}
type Provider interface{ Current() *model.AgentConfig }
type boundedProbe struct{ bytes []byte }

func orgKeyHash(value string) string {
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:])
}

func (b *boundedProbe) Write(data []byte) (int, error) {
	n := len(data)
	remaining := 64*1024 - len(b.bytes)
	if remaining > 0 {
		b.bytes = append(b.bytes, data[:min(remaining, n)]...)
	}
	return n, nil
}

type Manager struct {
	mu             sync.RWMutex
	policy         Policy
	items          map[string]Item
	path           string
	logRoot        string
	client         *http.Client
	pressure       atomic.Bool
	limiter        *bandwidthLimiter
	kopiaSupported bool
}

var current atomic.Pointer[Manager]

func Validate(p Policy) error {
	s := p.Settings
	if p.Revision < 1 || s.CPU < 1 || s.CPU > 4096 || s.Memory < 128 || s.Memory > 16*1024*1024 ||
		s.Bandwidth < 1 || s.Bandwidth > 9999 || s.Retention < 1 || s.Retention > 365 ||
		s.LogSpace < 64 || s.LogSpace > 8192 {
		return errors.New("invalid performance policy bounds")
	}
	if s.Level != "debug" && s.Level != "info" && s.Level != "warning" && s.Level != "error" {
		return errors.New("invalid log level")
	}
	if s.Cache != nil && (*s.Cache < 0 || *s.Cache > 65536) {
		return errors.New("invalid cache budget")
	}
	return nil
}

func New(root string) *Manager {
	return &Manager{
		path:    filepath.Join(root, "runtime", "performance-policy.json"),
		logRoot: filepath.Join(root, "logs"),
		items:   make(map[string]Item),
		client: &http.Client{Timeout: 8 * time.Second, Transport: tlsclient.Transport(),
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }},
	}
}

// Run is a separate best-effort loop. Errors never propagate to Agent startup,
// the WebSocket session, task admission or lifecycle fences.
func (m *Manager) Run(ctx context.Context, provider Provider) {
	if root := provider.Current().LogDir; root != "" {
		m.logRoot = root
	}
	m.limiter = startBandwidthLimiter(ctx, m)
	probeCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	binary := provider.Current().KopiaPath
	if binary == "" {
		binary = "kopia"
	}
	var output boundedProbe
	command := exec.CommandContext(probeCtx, binary, "--help")
	command.Stdout = &output
	if err := command.Run(); err == nil {
		m.kopiaSupported = strings.Contains(string(output.bytes), "hfl-backup-quota-v1")
	}
	cancel()
	current.Store(m)
	if raw, err := readPolicyCache(m.path); err == nil {
		var cached cachedPolicy
		cfg := provider.Current()
		if len(raw) <= 4096 && json.Unmarshal(raw, &cached) == nil &&
			cached.NodeID == cfg.NodeID && cached.OrgKeyHash == orgKeyHash(cfg.OrgKey) &&
			cached.InstallationID == cfg.InstallationID {
			if cached.Origin != cfg.APIBaseURL {
				cached.Policy.Revision = 0
			}
			m.apply(ctx, cfg, cached.Policy, false)
		}
	}
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	nextSync, nextSweep := time.Time{}, time.Time{}
	nextRetry := time.Now().Add(5 * time.Minute)
	for {
		if ctx.Err() != nil {
			return
		}
		now := time.Now()
		if now.After(nextRetry) && m.Revision() > 0 {
			m.mu.RLock()
			p := m.policy
			cpu := m.items["cpu"]
			m.mu.RUnlock()
			if cpu.State == "pending" || cpu.State == "degraded" || cpu.State == "unsupported" {
				item := applyCPU(ctx, provider.Current(), p.Settings.CPU)
				m.set("cpu", item.State, item.Reason)
			}
			if err := logging.ApplyPolicy(p.Settings.Level, p.Settings.Retention, p.Settings.LogSpace/2); err == nil {
				for _, key := range []string{"logLevel", "retention", "logSpace"} {
					m.set(key, "applied", "Agent-owned runtime logging policy.")
				}
			}
			nextRetry = now.Add(5 * time.Minute)
		}
		if now.After(nextSync) {
			m.sync(ctx, provider.Current())
			nextSync = now.Add(30 * time.Second)
		}
		if now.After(nextSweep) {
			if m.Revision() > 0 {
				logging.DiscoverServerLogs(filepath.Join(filepath.Dir(m.path), "workspace", "server-sessions"))
				m.mu.RLock()
				settings := m.policy.Settings
				m.mu.RUnlock()
				err := logging.Sweep(now)
				kopiaErr := logging.SweepKopiaLogs(filepath.Join(m.logRoot, "kopia"), settings.Retention, settings.LogSpace/2, now)
				if err != nil || kopiaErr != nil {
					m.set("logSpace", "degraded", "Log cleanup unavailable; business operations continue.")
				} else {
					m.set("logSpace", "applied", "Agent-owned runtime log soft disk budget.")
				}
			}
			nextSweep = now.Add(time.Minute)
		}
		m.sampleMemory(ctx)
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func readPolicyCache(path string) ([]byte, error) {
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() > 4096 {
		return nil, errors.New("performance cache is unavailable or too large")
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	return io.ReadAll(io.LimitReader(file, 4097))
}

func (m *Manager) Revision() int64 { m.mu.RLock(); defer m.mu.RUnlock(); return m.policy.Revision }
func (m *Manager) set(key, state, reason string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.items[key] = Item{state, reason}
}
func (m *Manager) Report() Report {
	m.mu.RLock()
	defer m.mu.RUnlock()
	items := make(map[string]Item, len(m.items))
	for key, value := range m.items {
		items[key] = value
	}
	return Report{Revision: m.policy.Revision, Items: items}
}

func (m *Manager) apply(ctx context.Context, cfg *model.AgentConfig, p Policy, persist bool) {
	if Validate(p) != nil || p.Revision <= m.Revision() {
		return
	}
	if persist {
		raw, err := json.Marshal(cachedPolicy{
			NodeID:         cfg.NodeID,
			OrgKeyHash:     orgKeyHash(cfg.OrgKey),
			InstallationID: cfg.InstallationID,
			Origin:         cfg.APIBaseURL,
			Policy:         p,
		})
		if err != nil || atomicfile.Write(m.path, raw, 0600) != nil {
			// Do not acknowledge or install an unpersisted revision; retry later.
			return
		}
	}
	m.mu.Lock()
	m.policy = p
	m.mu.Unlock()
	cpu := applyCPU(ctx, cfg, p.Settings.CPU)
	m.set("cpu", cpu.State, cpu.Reason)
	m.set("memory", "monitoring", "Shared resident-memory soft budget; never terminates running work.")
	if m.limiter != nil && m.kopiaSupported {
		m.set("bandwidth", "pending", "Shared budget will be used by managed backup uploads and new repository server sessions.")
	} else if !m.kopiaSupported {
		m.set("bandwidth", "unsupported", "Kopia does not advertise shared backup quota support; transfers remain unrestricted.")
	} else {
		m.set("bandwidth", "degraded", "Local limiter unavailable; backup transfers remain unrestricted.")
	}
	if p.Settings.Cache != nil {
		m.set("cache", "pending", "Applies on the next safe managed repository operation.")
	} else {
		m.set("cache", "disabled", "Repository cache policy is not applicable to this Agent role.")
	}
	if err := logging.ApplyPolicy(p.Settings.Level, p.Settings.Retention, p.Settings.LogSpace/2); err != nil {
		for _, key := range []string{"logLevel", "retention", "logSpace"} {
			m.set(key, "degraded", "Runtime logging policy unavailable; previous logging continues.")
		}
	} else {
		for _, key := range []string{"logLevel", "retention", "logSpace"} {
			m.set(key, "applied", "Agent-owned runtime logs only.")
		}
	}
}

func (m *Manager) clear(ctx context.Context, cfg *model.AgentConfig) {
	_ = os.Remove(m.path)
	if m.Revision() == 0 {
		m.pressure.Store(false)
		return
	}
	m.mu.Lock()
	m.policy = Policy{}
	m.mu.Unlock()
	m.pressure.Store(false)
	cpu := clearCPU(ctx, cfg)
	m.set("cpu", cpu.State, cpu.Reason)
	_ = logging.ApplyPolicy("info", 30, 500)
	for _, key := range []string{"logLevel", "retention", "logSpace"} {
		m.set(key, "disabled", "Optional runtime logging policy disabled; default logging continues.")
	}
	m.set("memory", "disabled", "Optional memory governance disabled; core work continues.")
	m.set("bandwidth", "disabled", "Optional backup bandwidth governance disabled; transfers continue normally.")
	m.set("cache", "disabled", "Existing repository cache configuration is preserved.")
}

// KopiaLogEnv is used only by managed repository operations with a verified
// bundled Kopia. Half of the total log budget is reserved for Kopia CLI/content
// logs, the other half for Agent and runtime server writers.
func KopiaLogEnv(env map[string]string) map[string]string {
	m := current.Load()
	result := make(map[string]string, len(env)+1)
	for key, value := range env {
		result[key] = value
	}
	delete(result, "HFL_RUNTIME_LOG_POLICY")
	if m == nil || !m.kopiaSupported {
		return result
	}
	m.mu.RLock()
	settings := m.policy.Settings
	active := m.policy.Revision > 0
	m.mu.RUnlock()
	if !active {
		return result
	}
	raw, err := json.Marshal(map[string]any{
		"level": settings.Level, "dir": filepath.Join(m.logRoot, "kopia"),
		"days": settings.Retention, "budget_mb": settings.LogSpace / 2,
	})
	if err != nil {
		return result
	}
	result["HFL_RUNTIME_LOG_POLICY"] = string(raw)
	return result
}

func (m *Manager) sync(ctx context.Context, cfg *model.AgentConfig) {
	if cfg == nil || cfg.APIBaseURL == "" || cfg.NodeID == "" || cfg.NodeToken == "" {
		return
	}
	endpoint, err := url.Parse(strings.TrimRight(cfg.APIBaseURL, "/") + "/api/v1/node/agent/performance-policy")
	if err != nil || endpoint.Host == "" || endpoint.User != nil ||
		endpoint.Fragment != "" || endpoint.RawQuery != "" ||
		(endpoint.Scheme != "https" && endpoint.Scheme != "http") ||
		(endpoint.Scheme != "https" && !tlsclient.InsecureTLSEnabled()) {
		return
	}
	query := endpoint.Query()
	query.Set("node_id", cfg.NodeID)
	endpoint.RawQuery = query.Encode()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return
	}
	request.Header.Set("X-Org-Key", cfg.OrgKey)
	request.Header.Set("X-Node-Token", cfg.NodeToken)
	response, err := m.client.Do(request)
	if err != nil {
		return
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return
	}
	var body map[string]json.RawMessage
	if json.NewDecoder(io.LimitReader(response.Body, 8193)).Decode(&body) != nil {
		return
	}
	rawPolicy, exists := body["policy"]
	if !exists {
		return // An unrelated or malformed 200 response is not policy removal.
	}
	if strings.TrimSpace(string(rawPolicy)) == "null" {
		m.clear(ctx, cfg)
		return
	}
	var policy Policy
	if json.Unmarshal(rawPolicy, &policy) != nil {
		return
	}
	m.apply(ctx, cfg, policy, true)
	report := m.Report()
	if m.Revision() < policy.Revision {
		report.Revision = policy.Revision
		for _, key := range []string{"cpu", "memory", "bandwidth", "cache", "logLevel", "retention", "logSpace"} {
			report.Items[key] = Item{"degraded", "Policy could not be validated or persisted; previous runtime behavior continues."}
		}
	} else if m.Revision() > policy.Revision {
		report.ObservedRevision = m.Revision()
		report.Revision = policy.Revision
		for _, key := range []string{"cpu", "memory", "bandwidth", "cache", "logLevel", "retention", "logSpace"} {
			report.Items[key] = Item{"degraded", "Agent has a newer cached revision; save settings again to reconcile safely."}
		}
	}
	raw, err := json.Marshal(report)
	if err != nil {
		return
	}
	request, err = http.NewRequestWithContext(ctx, http.MethodPost, endpoint.String(), strings.NewReader(string(raw)))
	if err != nil {
		return
	}
	request.Header.Set("X-Org-Key", cfg.OrgKey)
	request.Header.Set("X-Node-Token", cfg.NodeToken)
	request.Header.Set("Content-Type", "application/json")
	response2, err := m.client.Do(request)
	if err == nil {
		_ = response2.Body.Close()
	}
}

func (m *Manager) sampleMemory(ctx context.Context) {
	m.mu.RLock()
	budget := m.policy.Settings.Memory
	m.mu.RUnlock()
	if budget == 0 {
		return
	}
	proc, err := gprocess.NewProcess(int32(os.Getpid()))
	if err != nil {
		return
	}
	var rss uint64
	queue := []*gprocess.Process{proc}
	seen := make(map[int32]bool)
	for len(queue) > 0 && len(seen) < 4096 {
		p := queue[0]
		queue = queue[1:]
		if seen[p.Pid] {
			continue
		}
		seen[p.Pid] = true
		if info, err := p.MemoryInfoWithContext(ctx); err == nil {
			rss += info.RSS
		} else if p.Pid == int32(os.Getpid()) {
			m.pressure.Store(false)
			m.set("memory", "degraded", "Process memory sampling unavailable; no work is blocked.")
			return
		}
		children, err := p.ChildrenWithContext(ctx)
		if err != nil {
			m.pressure.Store(false)
			m.set("memory", "degraded", "Process-tree sampling is incomplete; no work is blocked.")
			return
		}
		queue = append(queue, children...)
	}
	if len(queue) > 0 {
		m.set("memory", "degraded", "Process-tree sampling bound exceeded; no work is blocked.")
		m.pressure.Store(false)
		return
	}
	high := rss > uint64(budget)*1024*1024
	m.pressure.Store(high)
	reason := "Shared RSS soft budget; no OOM limit or process termination."
	if high {
		reason = fmt.Sprintf("Soft budget exceeded (%d MB); new subprocess starts are briefly paced, running work continues.", rss/1024/1024)
	}
	m.set("memory", "monitoring", reason)
}

// BeforeWork adds bounded, fail-open pressure relief, never an admission error.
func BeforeWork(ctx context.Context) {
	m := current.Load()
	if m == nil || !m.pressure.Load() {
		return
	}
	timer := time.NewTimer(200 * time.Millisecond)
	defer timer.Stop()
	select {
	case <-ctx.Done():
	case <-timer.C:
	}
}

func CacheApplied(sizeMB int, ok bool) {
	if m := current.Load(); m != nil && m.Revision() > 0 {
		if ok {
			m.mu.RLock()
			desired := m.policy.Settings.Cache
			m.mu.RUnlock()
			state := "applied"
			if desired != nil && *desired != sizeMB {
				state = "pending"
			}
			m.set("cache", state, fmt.Sprintf("Latest confirmed managed repository cache budget: %d MB.", sizeMB))
		} else {
			m.set("cache", "degraded", "Cache update failed; previous connection configuration remains in use.")
		}
	}
}

func CacheConfigured() bool {
	m := current.Load()
	if m == nil {
		return false
	}
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.policy.Revision > 0 && m.policy.Settings.Cache != nil
}
