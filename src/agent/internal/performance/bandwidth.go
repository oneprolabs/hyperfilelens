package performance

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"net"
	"net/http"
	"sync"
	"time"
)

const quotaChunk = 64 * 1024

type bandwidthLimiter struct {
	mu      sync.Mutex
	address string
	token   string
	tokens  float64
	last    time.Time
	waiting map[string]time.Time
	order   []string
}

func startBandwidthLimiter(ctx context.Context, m *Manager) *bandwidthLimiter {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil
	}
	secret := make([]byte, 32)
	if _, err := rand.Read(secret); err != nil {
		_ = listener.Close()
		return nil
	}
	b := &bandwidthLimiter{address: "http://" + listener.Addr().String(), token: hex.EncodeToString(secret),
		last: time.Now(), waiting: make(map[string]time.Time)}
	server := &http.Server{ReadHeaderTimeout: time.Second, ReadTimeout: time.Second, WriteTimeout: time.Second,
		MaxHeaderBytes: 4096, Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Method != "POST" || r.URL.Path != "/quota" ||
				subtle.ConstantTimeCompare([]byte(r.Header.Get("X-HFL-Quota-Token")), []byte(b.token)) != 1 {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
			var request struct {
				Bytes int    `json:"bytes"`
				Task  string `json:"task"`
			}
			if json.NewDecoder(http.MaxBytesReader(w, r.Body, 1024)).Decode(&request) != nil ||
				request.Bytes < 1 || request.Bytes > quotaChunk || len(request.Task) > 128 {
				http.Error(w, "invalid quota request", http.StatusBadRequest)
				return
			}
			m.mu.RLock()
			rate := m.policy.Settings.Bandwidth
			m.mu.RUnlock()
			granted := b.grant(request.Bytes, request.Task, float64(rate)*1_000_000, time.Now())
			if granted < 0 {
				granted = request.Bytes
				m.set("bandwidth", "degraded", "Quota coordination capacity exceeded; backup transfer proceeds.")
			} else {
				m.set("bandwidth", "applied", "Shared managed backup-upload budget; control and restore traffic excluded.")
			}
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]int{"granted": granted, "retry_ms": 25})
		})}
	go func() { _ = server.Serve(listener) }()
	go func() { <-ctx.Done(); _ = server.Close() }()
	return b
}

func (b *bandwidthLimiter) grant(bytes int, task string, rate float64, now time.Time) int {
	b.mu.Lock()
	defer b.mu.Unlock()
	if rate <= 0 {
		return bytes
	}
	burst := max(float64(quotaChunk), rate/10)
	b.tokens = min(burst, b.tokens+max(0, now.Sub(b.last).Seconds())*rate)
	b.last = now
	for len(b.order) > 0 && now.Sub(b.waiting[b.order[0]]) > 2*time.Second {
		delete(b.waiting, b.order[0])
		b.order = b.order[1:]
	}
	if _, exists := b.waiting[task]; !exists {
		if len(b.order) >= 1024 {
			return -1
		} // bounded coordinator, fail open.
		b.order = append(b.order, task)
	}
	b.waiting[task] = now
	if len(b.order) > 0 && b.order[0] == task && b.tokens >= float64(bytes) {
		b.tokens -= float64(bytes)
		delete(b.waiting, task)
		b.order = b.order[1:]
		return bytes
	}
	return 0
}

// BackupEnv is added ONLY to managed snapshot upload commands. It is never
// injected into restores, control traffic, upgrades or repository maintenance.
func BackupEnv(task string, env map[string]string) map[string]string {
	m := current.Load()
	copyEnv := make(map[string]string, len(env)+3)
	for key, value := range env {
		copyEnv[key] = value
	}
	for _, key := range []string{
		"HFL_BACKUP_DATA",
		"HFL_BACKUP_QUOTA_URL",
		"HFL_BACKUP_QUOTA_TOKEN",
		"HFL_BACKUP_QUOTA_TASK",
		"HFL_BACKUP_QUOTA_MODE",
		"HFL_RUNTIME_LOG_POLICY",
	} {
		delete(copyEnv, key)
	}
	copyEnv["HFL_BACKUP_DATA"] = "1"
	if m == nil || m.limiter == nil || !m.kopiaSupported || m.Revision() == 0 {
		return copyEnv
	}
	copyEnv["HFL_BACKUP_QUOTA_URL"] = m.limiter.address
	copyEnv["HFL_BACKUP_QUOTA_TOKEN"] = m.limiter.token
	copyEnv["HFL_BACKUP_QUOTA_TASK"] = task
	return copyEnv
}

// ServerEnv only enables per-session pacing for explicitly marked backup data.
// Unmarked restore/download requests do not share this budget.
func ServerEnv(session string, env map[string]string) map[string]string {
	m := current.Load()
	if m == nil || m.limiter == nil || !m.kopiaSupported || m.Revision() == 0 {
		result := make(map[string]string, len(env))
		for key, value := range env {
			result[key] = value
		}
		for _, key := range []string{
			"HFL_BACKUP_DATA",
			"HFL_BACKUP_QUOTA_URL",
			"HFL_BACKUP_QUOTA_TOKEN",
			"HFL_BACKUP_QUOTA_TASK",
			"HFL_BACKUP_QUOTA_MODE",
			"HFL_RUNTIME_LOG_POLICY",
		} {
			delete(result, key)
		}
		return result
	}
	result := BackupEnv("server:"+session, env)
	delete(result, "HFL_BACKUP_DATA")
	result["HFL_BACKUP_QUOTA_MODE"] = "server"
	return result
}

func Throttling(task string) bool {
	m := current.Load()
	if m == nil || m.limiter == nil {
		return false
	}
	b := m.limiter
	b.mu.Lock()
	defer b.mu.Unlock()
	return time.Since(b.waiting[task]) < 2*time.Second
}

func BandwidthDegraded() {
	if m := current.Load(); m != nil && m.Revision() > 0 {
		m.set("bandwidth", "degraded", "Optional pacing was unavailable; backup transfer continued unrestricted.")
	}
}
