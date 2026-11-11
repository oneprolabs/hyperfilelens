package kopialayout

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"hyperfilelens/agent/internal/platform/vfs"
)

// ProcessEnv makes log placement independent of optional performance policy and
// prevents Kopia's OS-specific fallbacks from opening a second managed cache.
func ProcessEnv(root, logDir string, input map[string]string) (map[string]string, error) {
	root, err := filepath.Abs(root)
	if err != nil {
		return nil, err
	}
	cache := vfs.AgentCacheDir(root)
	logDir, err = filepath.Abs(logDir)
	if err != nil {
		return nil, err
	}
	if err := separateLogs(root, logDir); err != nil {
		return nil, err
	}
	env := make(map[string]string, len(input)+8)
	for key, value := range input {
		env[key] = value
	}
	effective := strings.TrimSpace(env["KOPIA_CACHE_DIRECTORY"])
	if effective == "" {
		effective = cache
	}
	if !filepath.IsAbs(effective) || !samePath(cache, effective) && !within(cache, effective) {
		return nil, fmt.Errorf("Kopia cache must be inside the installation's managed cache root")
	}
	if err := plainPath(root, effective); err != nil {
		return nil, err
	}
	if err := plainPath(root, filepath.Join(effective, "server-contents")); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Join(effective, "server-contents"), 0o700); err != nil {
		return nil, err
	}
	if err := privateDir(logDir, filepath.Join(logDir, "kopia", "cli-logs")); err != nil {
		return nil, err
	}
	if err := privateDir(logDir, filepath.Join(logDir, "kopia", "content-logs")); err != nil {
		return nil, err
	}
	env["KOPIA_CACHE_DIRECTORY"] = effective
	env["KOPIA_LOG_DIR"] = filepath.Join(logDir, "kopia")
	if _, supplied := env["HFL_RUNTIME_LOG_POLICY"]; !supplied {
		env["HFL_RUNTIME_LOG_POLICY"] = "" // Do not inherit a second log destination.
	}
	env["XDG_CACHE_HOME"] = cache
	if runtime.GOOS == "windows" {
		env["LOCALAPPDATA"] = cache
		if strings.TrimSpace(env["USERPROFILE"]) == "" {
			env["USERPROFILE"] = root
		}
	}
	if strings.TrimSpace(env["HOME"]) == "" {
		env["HOME"] = root
	}
	if strings.TrimSpace(env["KOPIA_USE_KEYRING"]) == "" {
		env["KOPIA_USE_KEYRING"] = "false"
	}
	if strings.TrimSpace(env["KOPIA_PERSIST_CREDENTIALS_ON_CONNECT"]) == "" {
		env["KOPIA_PERSIST_CREDENTIALS_ON_CONNECT"] = "false"
	}
	return env, nil
}
