package kopialayout

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"hyperfilelens/agent/internal/platform/filelock"
	"hyperfilelens/agent/internal/platform/vfs"
)

const maxConfigBytes = 8 << 20
const maxLogBytes = 256 << 20

type migrationEntry struct {
	Source     string `json:"source"` // Relative to cache; never an arbitrary deletion path.
	Kind       string `json:"kind"`
	SourceHash string `json:"source_sha256"`
	TargetHash string `json:"target_sha256"`
	Phase      string `json:"phase"`
	Modified   int64  `json:"modified_unix_nano,omitempty"`
}

type migrationState struct {
	Version  int                        `json:"version"`
	Status   string                     `json:"status"`
	Entries  map[string]*migrationEntry `json:"entries"`
	Retained []string                   `json:"retained,omitempty"`
	LogRoot  string                     `json:"log_root,omitempty"`
}

// IncompleteError means known files are migrated but unclassified files remain.
// Operations may use the new layout, but the cache is NOT declared disposable.
type IncompleteError struct{ Count int }

func (e *IncompleteError) Error() string {
	return fmt.Sprintf("CACHE_LAYOUT_INCOMPLETE: %d unclassified cache entries retained; see the private migration record", e.Count)
}

type referenceSwitcher func(context.Context, map[string]string) error

// Status exposes only non-secret operational metadata, never configuration,
// connection fingerprints, source checksums or task payloads.
func Status(root string) (map[string]any, error) {
	result := map[string]any{
		"status": "pending", "disposable": false,
		"cache_root": vfs.AgentCacheDir(root), "state_file": StatePath(root),
	}
	data, err := readRegular(root, StatePath(root), 32<<20)
	if os.IsNotExist(err) {
		return result, nil
	}
	if err != nil {
		return nil, err
	}
	var state migrationState
	if err := json.Unmarshal(data, &state); err != nil || state.Version != 1 || state.Entries == nil {
		return nil, fmt.Errorf("invalid cache-layout migration record")
	}
	result["status"] = state.Status
	result["disposable"] = state.Status == "completed"
	result["retained_entries"] = len(state.Retained)
	return result, nil
}

// Ready is used only by CLI entrypoints sharing an already initialized service.
// Incomplete inventory is deliberately not reported as a disposable cache.
func Ready(root string) (bool, error) {
	data, err := readRegular(root, StatePath(root), 32<<20)
	if os.IsNotExist(err) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	var state migrationState
	if err := json.Unmarshal(data, &state); err != nil || state.Version != 1 || state.Entries == nil {
		return false, fmt.Errorf("invalid cache-layout migration record")
	}
	return state.Status == "completed" || state.Status == "incomplete", nil
}

// Migrate runs before task dispatch. The callback switches durable CLI aliases
// in one database transaction before any old configuration is removed.
func Migrate(ctx context.Context, root, logDir string, switchReferences func(context.Context, map[string]string) error) error {
	root, err := filepath.Abs(root)
	if err != nil {
		return err
	}
	logDir, err = filepath.Abs(logDir)
	if err != nil {
		return err
	}
	m := migrator{root: root, logs: logDir, switchReferences: switchReferences, checkUsers: CheckUsers}
	return m.run(ctx)
}

type migrator struct {
	root             string
	logs             string
	state            migrationState
	switchReferences referenceSwitcher
	checkUsers       func(context.Context, string, bool) error
	after            func(string) error // Fault injection at durable boundaries.
}

func (m *migrator) checkpoint(stage string) error {
	if m.after != nil {
		return m.after(stage)
	}
	return nil
}

func (m *migrator) save() error {
	data, err := json.MarshalIndent(m.state, "", "  ")
	if err != nil {
		return err
	}
	return writeDurable(m.root, StatePath(m.root), data)
}

func (m *migrator) run(ctx context.Context) error {
	cache := vfs.AgentCacheDir(m.root)
	if err := separateLogs(m.root, m.logs); err != nil {
		return err
	}
	if err := privateDir(m.root, RuntimeDir(m.root)); err != nil {
		return err
	}
	release, err := filelock.Acquire(ctx, filepath.Join(RuntimeDir(m.root), "migration.lock"), false)
	if err != nil {
		return err
	}
	defer release()
	m.state = migrationState{Version: 1, Status: "discovering", Entries: map[string]*migrationEntry{}}
	if data, err := readRegular(m.root, StatePath(m.root), 32<<20); err == nil {
		if err := json.Unmarshal(data, &m.state); err != nil || m.state.Version != 1 || m.state.Entries == nil {
			return fmt.Errorf("invalid cache-layout migration record; originals retained")
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	if m.state.LogRoot != "" && !samePath(m.state.LogRoot, m.logs) {
		for _, e := range m.state.Entries {
			if e != nil && e.Kind == "log" && e.Phase != "removed" {
				return fmt.Errorf("log destination changed during an unfinished migration; originals retained")
			}
		}
	}
	m.state.LogRoot = m.logs
	if err := plainPath(m.root, cache); err != nil {
		return err
	}
	// Check old processes before inventory, including the empty-cache case:
	// an active old Agent could otherwise write a legacy config after completion.
	if err := m.checkUsers(ctx, m.root, false); err != nil {
		return err
	}
	m.state.Status = "discovering"
	if err := m.save(); err != nil {
		return err
	}
	if err := m.discover(ctx); err != nil {
		return err
	}
	m.state.Status = "copying"
	if err := m.save(); err != nil {
		return err
	}
	if err := m.checkpoint("discovered"); err != nil {
		return err
	}

	keys := make([]string, 0, len(m.state.Entries))
	for key := range m.state.Entries {
		if m.state.Entries[key] == nil || key != m.state.Entries[key].Source {
			return fmt.Errorf("invalid migration entry")
		}
		keys = append(keys, key)
	}
	sort.Strings(keys)
	// Hold all surviving old prepare locks until the migration has committed.
	// Never take an unverified arbitrary file as a lock.
	for _, key := range keys {
		entry := m.state.Entries[key]
		if entry.Kind != "lock" && entry.Kind != "maintenance-lock" || entry.Phase == "removed" {
			continue
		}
		source, _, err := m.paths(entry)
		if err != nil {
			return err
		}
		if _, err := os.Lstat(source); os.IsNotExist(err) {
			continue
		}
		unlock, err := filelock.Acquire(ctx, source, false)
		if err != nil {
			return err
		}
		defer unlock()
	}
	if err := m.checkUsers(ctx, m.root, false); err != nil {
		return err
	}
	for _, key := range keys {
		if err := ctx.Err(); err != nil {
			return err
		}
		if err := m.copyEntry(m.state.Entries[key]); err != nil {
			return fmt.Errorf("cache-layout copy failed for a %s entry: %w", m.state.Entries[key].Kind, err)
		}
	}
	mappings := map[string]string{}
	for _, key := range keys {
		entry := m.state.Entries[key]
		if entry.Kind == "config" {
			source, target, err := m.paths(entry)
			if err != nil {
				return err
			}
			mappings[source] = target
		}
	}
	if m.switchReferences != nil {
		if err := m.switchReferences(ctx, mappings); err != nil {
			return fmt.Errorf("switch durable repository references: %w", err)
		}
	}
	m.state.Status = "references_switched"
	if err := m.save(); err != nil {
		return err
	}
	if err := m.checkpoint("references_switched"); err != nil {
		return err
	}
	for _, key := range keys {
		if err := ctx.Err(); err != nil {
			return err
		}
		if err := m.removeEntry(m.state.Entries[key]); err != nil {
			return fmt.Errorf("cache-layout cleanup pending: %w", err)
		}
	}
	m.state.Status = "completed"
	if len(m.state.Retained) != 0 {
		m.state.Status = "incomplete"
	}
	if err := m.save(); err != nil {
		return err
	}
	if len(m.state.Retained) != 0 {
		return &IncompleteError{Count: len(m.state.Retained)}
	}
	return nil
}

func (m *migrator) paths(e *migrationEntry) (string, string, error) {
	cache := vfs.AgentCacheDir(m.root)
	if e == nil || filepath.IsAbs(e.Source) || !within(cache, filepath.Join(cache, e.Source)) {
		return "", "", fmt.Errorf("invalid migration source")
	}
	source := filepath.Join(cache, e.Source)
	var target string
	switch e.Kind {
	case "config", "fingerprint":
		if filepath.Dir(source) != LegacyConfigDir(m.root) {
			return "", "", fmt.Errorf("invalid connection source")
		}
		name := filepath.Base(source)
		if e.Kind == "config" && !managedConfigName(name) ||
			e.Kind == "fingerprint" && !managedConfigName(strings.TrimSuffix(strings.TrimSuffix(name, ".part"), ".connection.sha256")) {
			return "", "", fmt.Errorf("invalid connection filename")
		}
		target = filepath.Join(ConfigDir(m.root), name)
	case "lock", "maintenance-lock":
		suffix := ".lock"
		if e.Kind == "maintenance-lock" {
			suffix = ".mlock"
		}
		config := strings.TrimSuffix(filepath.Base(source), suffix)
		if filepath.Dir(source) != LegacyConfigDir(m.root) || !managedConfigName(config) {
			return "", "", fmt.Errorf("invalid old lock")
		}
		target = LockPath(m.root, filepath.Join(ConfigDir(m.root), config))
		if e.Kind == "maintenance-lock" {
			// Kopia itself fixes this filename beside its config. Keep that
			// native contract rather than introducing symlinked locks or
			// requiring a new Kopia build just for the layout migration.
			target = filepath.Join(ConfigDir(m.root), config) + ".mlock"
		}
	case "log":
		parts := strings.Split(filepath.ToSlash(e.Source), "/")
		if len(parts) < 3 || !ownedLogName(filepath.Base(source)) {
			return "", "", fmt.Errorf("invalid historical log")
		}
		category := filepath.Base(filepath.Dir(source))
		if category != "cli-logs" && category != "content-logs" {
			return "", "", fmt.Errorf("invalid historical log category")
		}
		// Keep every source namespace distinct, including repeated filenames.
		sourceID := digest([]byte(filepath.ToSlash(filepath.Dir(e.Source))))[:16]
		target = filepath.Join(m.logs, "kopia", category, "legacy-"+sourceID, filepath.Base(source))
	case "log-alias":
		if filepath.Base(source) != "latest.log" || !logDirectory(filepath.Dir(e.Source)) {
			return "", "", fmt.Errorf("invalid log alias")
		}
		if err := plainPath(m.root, filepath.Dir(source)); err != nil {
			return "", "", err
		}
		return source, "", nil
	default:
		return "", "", fmt.Errorf("unknown migration entry kind")
	}
	if err := plainPath(m.root, source); err != nil {
		return "", "", err
	}
	targetRoot := m.root
	if e.Kind == "log" {
		targetRoot = m.logs
	}
	if err := plainPath(targetRoot, target); err != nil {
		return "", "", err
	}
	return source, target, nil
}

func (m *migrator) copyEntry(e *migrationEntry) error {
	if e.Phase == "removed" {
		return nil // The active target may legitimately have changed since commit.
	}
	source, target, err := m.paths(e)
	if err != nil {
		return err
	}
	if e.Kind == "log-alias" {
		e.Phase = "verified"
		return m.save()
	}
	root, limit := m.root, int64(maxConfigBytes)
	if e.Kind == "log" {
		limit = maxLogBytes
	}
	data, err := readRegular(m.root, source, limit)
	if os.IsNotExist(err) {
		// The atomic target may have been written immediately before a crash.
		// Its recorded hash is still sufficient to verify it below, even if
		// someone subsequently removed cache while migration was incomplete.
		if e.Kind == "lock" || e.Kind == "maintenance-lock" {
			if _, targetErr := os.Lstat(target); os.IsNotExist(targetErr) {
				if err := writeDurable(m.root, target, nil); err != nil {
					return err
				}
			}
		}
	} else if err != nil {
		return err
	} else if digest(data) != e.SourceHash {
		return fmt.Errorf("source changed; both copies retained")
	}
	if err == nil {
		replacement := data
		if e.Kind == "config" {
			replacement, err = relocatedConfig(m.root, source, data)
			if err != nil {
				return err
			}
		}
		if e.Kind == "lock" || e.Kind == "maintenance-lock" {
			replacement = nil // Lock state is never copied.
		}
		if digest(replacement) != e.TargetHash {
			return fmt.Errorf("replacement differs from the recorded migration")
		}
		if e.Kind == "log" {
			root = m.logs
		}
		existing, readErr := readRegular(root, target, limit)
		switch {
		case os.IsNotExist(readErr):
			if err := writeDurable(root, target, replacement); err != nil {
				return err
			}
		case readErr != nil:
			return readErr
		case digest(existing) != e.TargetHash:
			return fmt.Errorf("destination conflict; both copies retained")
		}
	}
	if e.Kind == "log" {
		root = m.logs
	}
	verified, err := readRegular(root, target, limit)
	if err != nil || digest(verified) != e.TargetHash {
		return fmt.Errorf("replacement verification failed; original retained")
	}
	if err := restrictPath(target, false); err != nil {
		return err
	}
	if e.Kind == "log" && e.Modified != 0 {
		modified := time.Unix(0, e.Modified)
		if err := os.Chtimes(target, modified, modified); err != nil {
			return err
		}
	}
	if err := m.checkpoint("target_written"); err != nil {
		return err
	}
	e.Phase = "verified"
	if err := m.save(); err != nil {
		return err
	}
	return m.checkpoint("verified")
}

func (m *migrator) removeEntry(e *migrationEntry) error {
	if e.Phase == "removed" {
		return nil
	}
	if e.Phase != "verified" {
		return fmt.Errorf("replacement not verified")
	}
	source, target, err := m.paths(e)
	if err != nil {
		return err
	}
	if e.Kind == "log-alias" {
		link, err := os.Readlink(source)
		if os.IsNotExist(err) {
			e.Phase = "removed"
			return m.save()
		}
		if err != nil || digest([]byte(link)) != e.SourceHash {
			return fmt.Errorf("log alias changed")
		}
		linked := filepath.Join(filepath.Dir(e.Source), link)
		owner := m.state.Entries[linked]
		if owner == nil || owner.Kind != "log" || owner.Phase != "removed" {
			return fmt.Errorf("log alias target not yet migrated")
		}
	} else {
		limit := int64(maxConfigBytes)
		if e.Kind == "log" {
			limit = maxLogBytes
		}
		targetRoot := m.root
		if e.Kind == "log" {
			targetRoot = m.logs
		}
		verified, err := readRegular(targetRoot, target, limit)
		if err != nil || digest(verified) != e.TargetHash {
			return fmt.Errorf("replacement changed before cleanup; original retained")
		}
		data, err := readRegular(m.root, source, limit)
		if err != nil && !os.IsNotExist(err) {
			return err
		}
		if err == nil && digest(data) != e.SourceHash {
			return fmt.Errorf("source changed before cleanup; retained")
		}
	}
	if err := os.Remove(source); err != nil {
		if !errors.Is(err, os.ErrNotExist) {
			return err
		}
	} else {
		if err := syncDir(filepath.Dir(source)); err != nil {
			return err
		}
	}
	if err := m.checkpoint("source_removed"); err != nil {
		return err
	}
	e.Phase = "removed"
	return m.save()
}
