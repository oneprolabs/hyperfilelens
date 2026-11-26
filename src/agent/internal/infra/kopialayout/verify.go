package kopialayout

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
)

// VerifyCacheConnection checks a rebased cache locally. Opening a repository
// (including `kopia cache info`) would require transient credentials and remote
// access that a cache relocation task intentionally does not possess.
func VerifyCacheConnection(ctx context.Context, root, config string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if !ManagedConfig(root, config) {
		return fmt.Errorf("cache verification requires a managed connection")
	}
	raw, err := readRegular(root, config, maxConfigBytes)
	if err != nil {
		return fmt.Errorf("read managed cache reference: %w", err)
	}
	var connection struct {
		Caching struct {
			Directory string `json:"cacheDirectory"`
		} `json:"caching"`
	}
	if err := json.Unmarshal(raw, &connection); err != nil {
		// Never include configuration contents (which may contain credentials).
		return fmt.Errorf("managed connection has invalid cache settings")
	}
	active, err := ReadActiveRoot(root)
	if err != nil {
		return err
	}
	expected := CacheDir(root, config)
	if connection.Caching.Directory == "" || !samePath(connection.Caching.Directory, expected) {
		return fmt.Errorf("managed connection does not reference the active cache directory")
	}
	if err := privateDir(active.Path, expected); err != nil {
		return fmt.Errorf("prepare new cache directory: %w", err)
	}
	identity, err := directoryIdentity(expected)
	if err != nil {
		return err
	}
	probe, err := os.CreateTemp(expected, ".hfl-cache-probe-*")
	if err != nil {
		return fmt.Errorf("new cache directory is not writable: %w", err)
	}
	path := probe.Name()
	defer os.Remove(path) // Only the probe created by this verification, never cache data.
	payload := []byte("HyperFileLens local cache verification\n")
	if _, err := probe.Write(payload); err != nil {
		_ = probe.Close()
		return fmt.Errorf("write new cache probe: %w", err)
	}
	if err := probe.Sync(); err != nil {
		_ = probe.Close()
		return fmt.Errorf("sync new cache probe: %w", err)
	}
	if err := probe.Close(); err != nil {
		return fmt.Errorf("close new cache probe: %w", err)
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	restored, err := readRegular(active.Path, path, int64(len(payload)))
	if err != nil {
		return fmt.Errorf("read new cache probe: %w", err)
	}
	if !bytes.Equal(restored, payload) {
		return fmt.Errorf("new cache probe contents changed")
	}
	next, err := ReadActiveRoot(root)
	if err != nil || next != active {
		return fmt.Errorf("active cache identity changed during verification")
	}
	nextIdentity, err := directoryIdentity(expected)
	if err != nil || nextIdentity != identity {
		return fmt.Errorf("repository cache identity changed during verification")
	}
	return ctx.Err()
}
