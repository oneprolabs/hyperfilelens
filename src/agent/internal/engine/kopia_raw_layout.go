package engine

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"hyperfilelens/agent/internal/infra/kopialayout"
)

// rawKopiaLayout covers CLI aliases and legacy task entrypoints as well as the
// managed-repository path. Command flags take precedence over environment, so
// merely setting KOPIA_CACHE_DIRECTORY would not be a sufficient safety boundary.
func (e *Engine) rawKopiaLayout(args []string, input map[string]string) ([]string, string, map[string]string, error) {
	args = append([]string(nil), args...)
	env := make(map[string]string, len(input)+2)
	for key, value := range input {
		env[key] = value
	}
	root := agentDataDir(e.current())
	config := strings.TrimSpace(env["KOPIA_CONFIG_PATH"])
	if config == "" {
		config = strings.TrimSpace(os.Getenv("KOPIA_CONFIG_PATH"))
	}
	for i := 0; i < len(args); i++ {
		key, value, inline := strings.Cut(args[i], "=")
		if key != "--config-file" && key != "--cache-directory" && key != "--log-dir" {
			continue
		}
		if !inline {
			if i+1 >= len(args) {
				return nil, "", nil, fmt.Errorf("missing Kopia path flag value")
			}
			value = args[i+1]
		}
		if key == "--config-file" {
			config = kopialayout.ResolveConfig(root, value)
			if inline {
				args[i] = key + "=" + config
			} else {
				args[i+1] = config
			}
		} else {
			// Cache/log destinations are owned by the Agent, not raw flags.
			return nil, "", nil, fmt.Errorf("Kopia cache and log paths are managed by the Agent")
		}
		if !inline {
			i++
		}
	}
	if config == "" {
		return nil, "", nil, fmt.Errorf("an explicit Kopia configuration is required")
	}
	config = kopialayout.ResolveConfig(root, config)
	config, err := filepath.Abs(config)
	if err != nil {
		return nil, "", nil, err
	}
	cacheRoot := filepath.Dir(managedRepositoryCacheRoot(e.current()))
	rel, err := filepath.Rel(cacheRoot, config)
	if err != nil || rel == "." || rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel) {
		return nil, "", nil, fmt.Errorf("Kopia configuration must be outside cache")
	}
	if err := kopialayout.PrepareConfigDir(root, config); err != nil {
		return nil, "", nil, err
	}
	env["KOPIA_CONFIG_PATH"] = config
	return args, config, env, nil
}
