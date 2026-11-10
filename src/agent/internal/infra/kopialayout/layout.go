// Package kopialayout separates persistent Kopia connections from disposable
// caches without changing historical connection filenames or warm-cache names.
package kopialayout

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"hyperfilelens/agent/internal/platform/filelock"
	"hyperfilelens/agent/internal/platform/vfs"
)

func ConfigDir(root string) string {
	return filepath.Join(vfs.AgentDataStoreDir(root), "kopia", "repositories")
}

func LegacyConfigDir(root string) string {
	return filepath.Join(vfs.AgentCacheDir(root), "repositories")
}

func RuntimeDir(root string) string {
	return filepath.Join(vfs.AgentRuntimeDir(root), "kopia")
}

func StatePath(root string) string {
	return filepath.Join(vfs.AgentDataStoreDir(root), "kopia", "migrations", "cache-layout-v1.json")
}

// ResolveConfig also handles persisted CLI aliases and redelivered task payloads
// containing an old managed path. External configurations are left untouched.
func ResolveConfig(root, config string) string {
	config = filepath.Clean(config)
	if samePath(filepath.Dir(config), LegacyConfigDir(root)) && managedConfigName(filepath.Base(config)) {
		return filepath.Join(ConfigDir(root), filepath.Base(config))
	}
	return config
}

func ManagedConfig(root, config string) bool {
	return samePath(filepath.Dir(config), ConfigDir(root)) && managedConfigName(filepath.Base(config))
}

func LockPath(root, config string) string {
	config = ResolveConfig(root, config)
	if ManagedConfig(root, config) {
		return filepath.Join(RuntimeDir(root), "repositories", filepath.Base(config)+".lock")
	}
	return config + ".lock"
}

// PrepareConfigDir validates the non-cache boundary before creating a config.
// Explicit external configs remain caller-owned, not migration candidates.
func PrepareConfigDir(root, config string) error {
	cache := filepath.Dir(LegacyConfigDir(root))
	absolute, err := filepath.Abs(config)
	if err != nil {
		return err
	}
	if samePath(cache, absolute) || within(cache, absolute) {
		return fmt.Errorf("Kopia configuration must be outside cache")
	}
	physicalCache, err := physicalPath(cache)
	if err != nil {
		return err
	}
	physicalConfig, err := physicalPath(absolute)
	if err != nil {
		return err
	}
	if samePath(physicalCache, physicalConfig) || within(physicalCache, physicalConfig) {
		return fmt.Errorf("Kopia configuration resolves inside cache")
	}
	if ManagedConfig(root, absolute) {
		return privateDir(root, filepath.Dir(absolute))
	}
	return os.MkdirAll(filepath.Dir(absolute), 0o700)
}

func PrepareLockDir(root, config string) error {
	lock := LockPath(root, config)
	if ManagedConfig(root, config) {
		return privateDir(root, filepath.Dir(lock))
	}
	return os.MkdirAll(filepath.Dir(lock), 0o700)
}

// CacheDir preserves the old absolute-path hash for relocated managed configs.
// This compatibility identity is not the path used to read configuration.
func CacheDir(root, config string) string {
	canonical, err := filepath.Abs(filepath.Clean(strings.TrimSpace(config)))
	if err != nil {
		canonical = filepath.Clean(config)
	}
	if ManagedConfig(root, canonical) {
		canonical = filepath.Join(LegacyConfigDir(root), filepath.Base(canonical))
		canonical, _ = filepath.Abs(canonical)
	}
	base := strings.TrimSuffix(filepath.Base(canonical), filepath.Ext(canonical))
	base = sanitizeToken(base)
	sum := sha256.Sum256([]byte(canonical))
	return filepath.Join(LegacyConfigDir(root), base+"-"+hex.EncodeToString(sum[:])[:12])
}

func samePath(a, b string) bool {
	aa, ea := filepath.Abs(a)
	bb, eb := filepath.Abs(b)
	return ea == nil && eb == nil && filepath.Clean(aa) == filepath.Clean(bb)
}

func sanitizeToken(value string) string {
	var out strings.Builder
	for _, c := range value {
		if c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_' {
			out.WriteRune(c)
		} else {
			out.WriteByte('-')
		}
	}
	value = strings.Trim(out.String(), "-")
	if value == "" {
		return "session"
	}
	if len(value) > 64 {
		return value[:64]
	}
	return value
}

// AcquireUsage covers the entire managed operation, not merely repository
// preparation. Startup migration takes the exclusive side of the same lock.
func AcquireUsage(ctx context.Context, root string) (func(), error) {
	if err := privateDir(root, RuntimeDir(root)); err != nil {
		return nil, err
	}
	return filelock.Acquire(ctx, filepath.Join(RuntimeDir(root), "migration.lock"), true)
}
