package kopialayout

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"regexp"
	"strings"
)

var configName = regexp.MustCompile(`^(?:repository|repo-[1-9][0-9]*(?:-nas-[a-f0-9]{16}|-server-[A-Za-z0-9_-]+)?|server-[A-Za-z0-9_-]+)(?:\.maintenance)?\.config$`)

func managedConfigName(name string) bool {
	return configName.MatchString(name)
}

// relocatedConfig validates a known managed filename AND the Kopia connection
// envelope. Only the cache path is rebased; unknown fields, client identity,
// encryption settings and cache budgets are retained as raw JSON.
func relocatedConfig(root, source string, data []byte) ([]byte, error) {
	if !managedConfigName(filepath.Base(source)) {
		return nil, fmt.Errorf("unrecognized connection name")
	}
	var config map[string]json.RawMessage
	if err := json.Unmarshal(data, &config); err != nil || config == nil {
		return nil, fmt.Errorf("invalid Kopia connection")
	}
	var storage struct {
		Type   string `json:"type"`
		Config struct {
			Path string `json:"path"`
		} `json:"config"`
	}
	var server struct {
		URL string `json:"url"`
	}
	_ = json.Unmarshal(config["storage"], &storage)
	_ = json.Unmarshal(config["apiServer"], &server)
	if storage.Type == "" && server.URL == "" {
		return nil, fmt.Errorf("unrecognized Kopia connection envelope")
	}
	if storage.Type == "filesystem" {
		// Real repositories are never cache, including an accidentally nested
		// repository. Do not turn its path into a deletable-cache assertion.
		if !filepath.IsAbs(storage.Config.Path) ||
			samePath(filepath.Dir(LegacyConfigDir(root)), storage.Config.Path) ||
			within(filepath.Dir(LegacyConfigDir(root)), storage.Config.Path) {
			return nil, fmt.Errorf("repository path is relative or overlaps cache")
		}
		// The configured path may be an external alias. Resolve existing path
		// components before deciding whether the cache can be discarded.
		cacheRoot, err := physicalPath(filepath.Dir(LegacyConfigDir(root)))
		if err != nil {
			return nil, fmt.Errorf("resolve cache root: %w", err)
		}
		repositoryRoot, err := physicalPath(storage.Config.Path)
		if err != nil {
			return nil, fmt.Errorf("resolve repository path: %w", err)
		}
		if samePath(cacheRoot, repositoryRoot) || within(cacheRoot, repositoryRoot) || within(repositoryRoot, cacheRoot) {
			return nil, fmt.Errorf("repository path resolves to a location overlapping cache")
		}
	}
	var caching map[string]json.RawMessage
	if err := json.Unmarshal(config["caching"], &caching); err != nil || caching == nil {
		return nil, fmt.Errorf("unrecognized Kopia cache settings")
	}
	var cache string
	if err := json.Unmarshal(caching["cacheDirectory"], &cache); err != nil || strings.TrimSpace(cache) == "" {
		return nil, fmt.Errorf("missing Kopia cache path")
	}
	if !filepath.IsAbs(cache) {
		cache = filepath.Join(filepath.Dir(source), cache)
	}
	// Keep the persisted path pointing at the same warm cache. The managed
	// process environment still supplies its historical connection-scoped path.
	caching["cacheDirectory"], _ = json.Marshal(filepath.Clean(cache))
	config["caching"], _ = json.Marshal(caching)
	return json.MarshalIndent(config, "", "  ")
}
