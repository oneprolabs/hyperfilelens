package kopialayout

import (
	"context"
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"hyperfilelens/agent/internal/platform/vfs"
)

var (
	logName       = regexp.MustCompile(`^kopia-[0-9]{8}-[0-9]{6}-[0-9]+-[A-Za-z0-9_-]+(?:\.[0-9]+)?\.log$`)
	cacheName     = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}-[a-f0-9]{12}$`)
	hexCacheName  = regexp.MustCompile(`^[a-f0-9]{16}$`)
	shardName     = regexp.MustCompile(`^[A-Za-z0-9_-]{1,4}$`)
	cacheBlobName = regexp.MustCompile(`^[A-Za-z0-9_-]+(?:\.[0-9]+)*\.f$`)
	cacheTempBlob = regexp.MustCompile(`^[A-Za-z0-9_-]+\.f\.tmp\.[A-Za-z0-9_-]+$`)
	indexName     = regexp.MustCompile(`^[A-Za-z0-9_-]+\.sndx$`)
	tempCacheName = regexp.MustCompile(`^tmp[0-9]+$`)
	fingerprint   = regexp.MustCompile(`^[a-f0-9]{64}\s*$`)
)

const cacheMarker = "Signature: 8a477f597d28d172789f06886806bc55"
const inventoryLimit = 250000

func ownedLogName(name string) bool { return logName.MatchString(name) }

func (m *migrator) discover(ctx context.Context) error {
	cache := vfs.AgentCacheDir(m.root)
	m.state.Retained = nil
	classified := map[string]bool{}
	configs := map[string]bool{}
	trustedCaches := map[string]bool{}
	entries, err := os.ReadDir(LegacyConfigDir(m.root))
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	for _, e := range entries {
		if !managedConfigName(e.Name()) || e.Type()&os.ModeSymlink != 0 || e.IsDir() {
			continue
		}
		source := filepath.Join(LegacyConfigDir(m.root), e.Name())
		data, err := readRegular(m.root, source, maxConfigBytes)
		if err != nil {
			continue
		}
		replacement, err := relocatedConfig(m.root, source, data)
		if err != nil {
			continue // Preserve malformed or unclassified connections.
		}
		if err := m.add(source, "config", data, replacement); err != nil {
			return err
		}
		configs[e.Name()] = true
		trusted, _ := filepath.Rel(cache, CacheDir(m.root, source))
		trustedCaches[trusted] = true
		classified[source] = true
	}
	// Resume sidecars after the corresponding old config has already gone.
	for _, e := range m.state.Entries {
		if e.Kind == "config" {
			configs[filepath.Base(e.Source)] = true
			trusted, _ := filepath.Rel(cache, CacheDir(m.root, filepath.Join(cache, e.Source)))
			trustedCaches[trusted] = true
		}
	}
	// Kopia does not always recreate CACHEDIR.TAG when the Agent has already
	// created a cache directory. A validated persistent config supplies the
	// ownership evidence for those historical and recreated cache namespaces.
	persistent, err := os.ReadDir(ConfigDir(m.root))
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	for _, entry := range persistent {
		if !managedConfigName(entry.Name()) || entry.IsDir() {
			continue
		}
		path := filepath.Join(ConfigDir(m.root), entry.Name())
		data, err := readRegular(m.root, path, maxConfigBytes)
		if err != nil {
			continue
		}
		if _, err := relocatedConfig(m.root, path, data); err != nil {
			continue
		}
		trusted, _ := filepath.Rel(cache, CacheDir(m.root, path))
		trustedCaches[trusted] = true
	}
	for _, e := range entries {
		source := filepath.Join(LegacyConfigDir(m.root), e.Name())
		if classified[source] || e.IsDir() || e.Type()&os.ModeSymlink != 0 {
			continue
		}
		name := e.Name()
		config := strings.TrimSuffix(name, ".lock")
		kind := ""
		if config != name && managedConfigName(config) {
			kind = "lock"
		}
		if config := strings.TrimSuffix(name, ".mlock"); config != name && managedConfigName(config) {
			kind = "maintenance-lock"
		}
		if strings.HasSuffix(name, ".connection.sha256") || strings.HasSuffix(name, ".connection.sha256.part") {
			config = strings.TrimSuffix(strings.TrimSuffix(name, ".part"), ".connection.sha256")
			if configs[config] {
				kind = "fingerprint"
			}
		}
		if kind == "" {
			continue
		}
		data, err := readRegular(m.root, source, maxConfigBytes)
		if err != nil || kind == "fingerprint" && !fingerprint.Match(data) ||
			(kind == "lock" || kind == "maintenance-lock") && len(data) != 0 {
			continue
		}
		replacement := data
		if kind == "lock" || kind == "maintenance-lock" {
			replacement = nil
		}
		if err := m.add(source, kind, data, replacement); err != nil {
			return err
		}
		classified[source] = true
	}
	count := 0
	err = filepath.WalkDir(cache, func(path string, entry fs.DirEntry, walkErr error) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if os.IsNotExist(walkErr) && path == cache {
			return nil
		}
		if walkErr != nil {
			return walkErr
		}
		count++
		if count > inventoryLimit {
			return fmt.Errorf("cache inventory bound exceeded; migration not complete")
		}
		if path == cache {
			return nil
		}
		if classified[path] {
			return nil
		}
		rel, _ := filepath.Rel(cache, path)
		if entry.IsDir() {
			if knownCacheDirectory(rel) {
				return nil
			}
			m.state.Retained = append(m.state.Retained, rel)
			return filepath.SkipDir
		}
		if logDirectory(filepath.Dir(rel)) {
			if ownedLogName(entry.Name()) && entry.Type().IsRegular() {
				data, err := readRegular(m.root, path, maxLogBytes)
				if err == nil {
					return m.add(path, "log", data, data)
				}
			}
			if entry.Name() == "latest.log" && entry.Type()&os.ModeSymlink != 0 {
				link, err := os.Readlink(path)
				if err == nil && filepath.Base(link) == link && ownedLogName(link) {
					target := filepath.Join(filepath.Dir(path), link)
					if data, err := readRegular(m.root, target, maxLogBytes); err == nil {
						if err := m.add(target, "log", data, data); err != nil {
							return err
						}
						return m.add(path, "log-alias", []byte(link), nil)
					}
				}
			}
		}
		if entry.Type().IsRegular() && recognizedCacheFile(cache, rel, trustedCaches) {
			return nil
		}
		m.state.Retained = append(m.state.Retained, rel)
		return nil
	})
	if err != nil {
		return err
	}
	sort.Strings(m.state.Retained)
	return nil
}

func (m *migrator) add(source, kind string, data, target []byte) error {
	rel, err := filepath.Rel(vfs.AgentCacheDir(m.root), source)
	if err != nil {
		return err
	}
	if existing := m.state.Entries[rel]; existing != nil && existing.Phase != "removed" {
		if existing.SourceHash != digest(data) || existing.TargetHash != digest(target) || existing.Kind != kind {
			return fmt.Errorf("migration source changed since inventory; originals retained")
		}
		return nil
	}
	m.state.Entries[rel] = &migrationEntry{
		Source: rel, Kind: kind, SourceHash: digest(data), TargetHash: digest(target), Phase: "discovered",
	}
	if kind == "log" {
		info, err := os.Lstat(source)
		if err != nil {
			return err
		}
		m.state.Entries[rel].Modified = info.ModTime().UnixNano()
	}
	return nil
}

func logDirectory(rel string) bool {
	parts := strings.Split(filepath.ToSlash(rel), "/")
	if len(parts) == 2 && parts[0] == "kopia" {
		return parts[1] == "cli-logs" || parts[1] == "content-logs"
	}
	return len(parts) == 4 && parts[0] == "repositories" && cacheName.MatchString(parts[1]) &&
		parts[2] == "kopia" && (parts[3] == "cli-logs" || parts[3] == "content-logs")
}

func cacheParts(rel string) (base string, rest []string) {
	parts := strings.Split(filepath.ToSlash(rel), "/")
	if len(parts) >= 2 && (parts[0] == "repositories" && cacheName.MatchString(parts[1]) ||
		parts[0] == "kopia" && hexCacheName.MatchString(parts[1])) {
		return filepath.Join(parts[0], parts[1]), parts[2:]
	}
	// The old generic client used the cache root itself.
	return "", parts
}

func cacheCategory(value string) bool {
	switch value {
	case "contents", "metadata", "index-blobs", "indexes", "own-writes", "blob-list", "server-contents":
		return true
	}
	return false
}

func knownCacheDirectory(rel string) bool {
	if rel == "repositories" || rel == "kopia" || logDirectory(rel) {
		return true
	}
	base, rest := cacheParts(rel)
	if base != "" && len(rest) == 0 {
		return true
	}
	if base != "" && len(rest) == 1 && rest[0] == "kopia" {
		return true
	}
	if len(rest) == 0 || !cacheCategory(rest[0]) {
		return false
	}
	for _, part := range rest[1:] {
		if !shardName.MatchString(part) {
			return false
		}
	}
	return true
}

func recognizedCacheFile(root, rel string, trusted map[string]bool) bool {
	name := filepath.Base(rel)
	dataPath := filepath.Join(root, rel)
	parts := strings.Split(filepath.ToSlash(rel), "/")
	// Historical Agent versions could leave an empty connection lock directly
	// under cache/repositories after the corresponding connection was removed.
	// It is cache-local coordination state, not a durable connection lock.
	if len(parts) == 2 && parts[0] == "repositories" &&
		strings.HasSuffix(name, ".config.lock") &&
		managedConfigName(strings.TrimSuffix(name, ".lock")) {
		data, err := readRegular(root, dataPath, 8192)
		return err == nil && len(data) == 0
	}
	base, rest := cacheParts(rel)
	if name == "CACHEDIR.TAG" {
		data, err := readRegular(root, dataPath, 8192)
		return err == nil && strings.HasPrefix(string(data), cacheMarker)
	}
	if len(rest) == 1 && (name == "kopia.repository" || name == "kopia.blobcfg") {
		marker, err := readRegular(root, filepath.Join(root, base, "CACHEDIR.TAG"), 8192)
		data, err := readRegular(root, dataPath, maxConfigBytes)
		if err != nil {
			return false
		}
		if trusted[base] || strings.HasPrefix(string(marker), cacheMarker) {
			return name == "kopia.blobcfg" && len(data) > 0 || json.Valid(data)
		}
		// Older Kopia caches did not always contain CACHEDIR.TAG. A valid
		// repository metadata pair is sufficient ownership evidence for these
		// two native files; any other unclassified cache content remains
		// retained and therefore still prevents disposable=true.
		return name == "kopia.blobcfg" && len(data) > 0 || name == "kopia.repository" && json.Valid(data)
	}
	if len(rest) < 2 || !cacheCategory(rest[0]) {
		return false
	}
	for _, part := range rest[1 : len(rest)-1] {
		if !shardName.MatchString(part) {
			return false
		}
	}
	if name == ".shards" {
		data, err := readRegular(root, dataPath, 8192)
		return err == nil && json.Valid(data)
	}
	if rest[0] == "indexes" {
		return indexName.MatchString(name) || tempCacheName.MatchString(name)
	}
	return cacheBlobName.MatchString(name) || cacheTempBlob.MatchString(name) || tempCacheName.MatchString(name)
}
