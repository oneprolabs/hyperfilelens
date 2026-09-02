package engine

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"
)

const (
	defaultKopiaCacheSizeMB int64 = 1024
	maxKopiaCacheSizeMB     int64 = 65536
	kopiaCacheSetTimeout          = 30 * time.Second
)

// managedKopiaCachePolicy is a single product-level budget split between the
// content and metadata caches understood by Kopia.
type managedKopiaCachePolicy struct {
	Enabled    bool
	ContentMB  int64
	MetadataMB int64
}

func kopiaCachePolicyFromPayload(p Payload) (managedKopiaCachePolicy, error) {
	value := int64(defaultKopiaCacheSizeMB)
	if raw, present := p.Extra["kopia_cache_size_mb"]; present {
		parsed, ok := payloadIntValue(raw)
		if !ok {
			return managedKopiaCachePolicy{}, fmt.Errorf("KOPIA_CACHE_POLICY_INVALID: cache size must be an integer")
		}
		value = int64(parsed)
	}
	if value < 0 || value > maxKopiaCacheSizeMB {
		return managedKopiaCachePolicy{}, fmt.Errorf("KOPIA_CACHE_POLICY_INVALID: cache size must be between 0 and %d MB", maxKopiaCacheSizeMB)
	}
	if value == 0 {
		return managedKopiaCachePolicy{}, nil
	}
	content := (value * 3) / 4
	if content == 0 {
		content = 1
	}
	metadata := value - content
	return managedKopiaCachePolicy{Enabled: true, ContentMB: content, MetadataMB: metadata}, nil
}

func (p managedKopiaCachePolicy) flags() []string {
	if !p.Enabled {
		return []string{
			"--content-cache-size-mb=0",
			"--content-cache-size-limit-mb=0",
			"--metadata-cache-size-mb=0",
			"--metadata-cache-size-limit-mb=0",
		}
	}
	return []string{
		fmt.Sprintf("--content-cache-size-mb=%d", p.ContentMB),
		fmt.Sprintf("--content-cache-size-limit-mb=%d", p.ContentMB),
		fmt.Sprintf("--metadata-cache-size-mb=%d", p.MetadataMB),
		fmt.Sprintf("--metadata-cache-size-limit-mb=%d", p.MetadataMB),
	}
}

func (p managedKopiaCachePolicy) setArgs(configFile string) []string {
	args := []string{"--config-file=" + configFile, "cache", "set", "--no-auto-maintenance"}
	return append(args, p.flags()...)
}

type kopiaConfigCaching struct {
	ContentSoftBytes  int64 `json:"maxCacheSize"`
	ContentHardBytes  int64 `json:"contentCacheSizeLimitBytes"`
	MetadataSoftBytes int64 `json:"maxMetadataCacheSize"`
	MetadataHardBytes int64 `json:"metadataCacheSizeLimitBytes"`
}

type kopiaConfigCachePolicy struct {
	Caching *kopiaConfigCaching `json:"caching"`
}

func cacheSizeMatchesMB(value int64, sizeMB int64) bool {
	// Kopia repository connect/create historically stored MiB while `cache
	// set` stored decimal MB for the same CLI value. Accept both encodings so
	// an unchanged product policy does not rewrite a valid config on every
	// repository operation.
	return value == sizeMB*1_000_000 || value == sizeMB*1024*1024
}

func kopiaConfigMatchesCachePolicy(configFile string, policy managedKopiaCachePolicy) (bool, error) {
	raw, err := os.ReadFile(configFile)
	if err != nil {
		return false, err
	}
	var config kopiaConfigCachePolicy
	if err := json.Unmarshal(raw, &config); err != nil {
		return false, fmt.Errorf("read Kopia cache policy: %w", err)
	}
	if config.Caching == nil {
		return false, nil
	}
	wantContent := policy.ContentMB
	wantMetadata := policy.MetadataMB
	if !policy.Enabled {
		wantContent = 0
		wantMetadata = 0
	}
	return cacheSizeMatchesMB(config.Caching.ContentSoftBytes, wantContent) &&
		cacheSizeMatchesMB(config.Caching.ContentHardBytes, wantContent) &&
		cacheSizeMatchesMB(config.Caching.MetadataSoftBytes, wantMetadata) &&
		cacheSizeMatchesMB(config.Caching.MetadataHardBytes, wantMetadata), nil
}

func applyManagedKopiaCachePolicy(
	ctx context.Context,
	bin string,
	configFile string,
	env map[string]string,
	policy managedKopiaCachePolicy,
) error {
	if _, err := os.Stat(configFile); err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	matches, err := kopiaConfigMatchesCachePolicy(configFile, policy)
	if err != nil {
		return err
	}
	if matches {
		return nil
	}
	result, err := runProcessWithTimeout(ctx, kopiaCacheSetTimeout, bin, policy.setArgs(configFile), env, "")
	if err != nil {
		output := result.Stderr
		if strings.TrimSpace(output) == "" {
			output = result.Stdout
		}
		return fmt.Errorf("KOPIA_CACHE_POLICY_APPLY_FAILED: %w: %s", err, trimCommandOutput(output))
	}
	return nil
}

func trimCommandOutput(value string) string {
	value = strings.TrimSpace(value)
	if len(value) > 500 {
		return value[:500]
	}
	return value
}
