package engine

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"hyperfilelens/agent/internal/model"
)

func TestManagedRepositoryCacheDirIsStableAndConfigScoped(t *testing.T) {
	cfg := &model.AgentConfig{DataDir: t.TempDir()}
	firstConfig := filepath.Join(cfg.DataDir, "cache", "repositories", "repo-10.config")
	secondConfig := filepath.Join(cfg.DataDir, "cache", "repositories", "repo-11.config")

	first := managedRepositoryCacheDir(cfg, firstConfig)
	if repeated := managedRepositoryCacheDir(cfg, firstConfig); repeated != first {
		t.Fatalf("repository cache directory is not stable: %q != %q", repeated, first)
	}
	second := managedRepositoryCacheDir(cfg, secondConfig)
	if first == second {
		t.Fatalf("different repository configs share cache directory %q", first)
	}
	root := managedRepositoryCacheRoot(cfg)
	for _, cacheDir := range []string{first, second} {
		rel, err := filepath.Rel(root, cacheDir)
		if err != nil || rel == "." || rel == ".." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) {
			t.Fatalf("repository cache directory %q escapes root %q", cacheDir, root)
		}
	}
}

func TestEnsureKopiaProcessEnvUsesDataDirCache(t *testing.T) {
	cfg := &model.AgentConfig{DataDir: t.TempDir()}
	env, err := ensureKopiaProcessEnv(cfg, map[string]string{
		"KOPIA_CHECK_FOR_UPDATES": "false",
	})
	if err != nil {
		t.Fatal(err)
	}

	cacheDir := filepath.Join(cfg.DataDir, "cache")
	if got := env["KOPIA_CACHE_DIRECTORY"]; got != cacheDir {
		t.Fatalf("KOPIA_CACHE_DIRECTORY = %q, want %q", got, cacheDir)
	}
	if _, err := os.Stat(filepath.Join(cacheDir, "server-contents")); err != nil {
		t.Fatalf("expected server content cache dir: %v", err)
	}
	if _, err := os.Stat(filepath.Join(cacheDir, "kopia", "cli-logs")); err != nil {
		t.Fatalf("expected kopia cli log cache dir: %v", err)
	}
	if got := env["KOPIA_USE_KEYRING"]; got != "false" {
		t.Fatalf("KOPIA_USE_KEYRING = %q, want false", got)
	}
	if got := env["KOPIA_PERSIST_CREDENTIALS_ON_CONNECT"]; got != "false" {
		t.Fatalf("KOPIA_PERSIST_CREDENTIALS_ON_CONNECT = %q, want false", got)
	}

	switch runtime.GOOS {
	case "windows":
		if env["LOCALAPPDATA"] != cacheDir {
			t.Fatalf("LOCALAPPDATA = %q, want %q", env["LOCALAPPDATA"], cacheDir)
		}
		if env["USERPROFILE"] != cfg.DataDir {
			t.Fatalf("USERPROFILE = %q, want %q", env["USERPROFILE"], cfg.DataDir)
		}
	default:
		if env["HOME"] != cfg.DataDir {
			t.Fatalf("HOME = %q, want %q", env["HOME"], cfg.DataDir)
		}
		if env["XDG_CACHE_HOME"] != cacheDir {
			t.Fatalf("XDG_CACHE_HOME = %q, want %q", env["XDG_CACHE_HOME"], cacheDir)
		}
	}
}

func TestEnsureKopiaProcessEnvRespectsExistingValues(t *testing.T) {
	cfg := &model.AgentConfig{DataDir: t.TempDir()}
	customCache := filepath.Join(t.TempDir(), "custom-cache")
	env, err := ensureKopiaProcessEnv(cfg, map[string]string{
		"KOPIA_CACHE_DIRECTORY":                customCache,
		"HOME":                                 "/custom/home",
		"XDG_CACHE_HOME":                       "/custom/xdg",
		"LOCALAPPDATA":                         `C:\custom\local`,
		"USERPROFILE":                          `C:\custom\profile`,
		"KOPIA_USE_KEYRING":                    "true",
		"KOPIA_PERSIST_CREDENTIALS_ON_CONNECT": "true",
	})
	if err != nil {
		t.Fatal(err)
	}
	if env["KOPIA_CACHE_DIRECTORY"] != customCache {
		t.Fatalf("KOPIA_CACHE_DIRECTORY overwritten: %q", env["KOPIA_CACHE_DIRECTORY"])
	}
	if _, err := os.Stat(filepath.Join(customCache, "server-contents")); err != nil {
		t.Fatalf("expected custom server content cache dir: %v", err)
	}
	if _, err := os.Stat(filepath.Join(customCache, "kopia", "cli-logs")); err != nil {
		t.Fatalf("expected custom kopia cli log cache dir: %v", err)
	}
	if _, err := os.Stat(filepath.Join(cfg.DataDir, "cache", "kopia", "cli-logs")); err != nil {
		t.Fatalf("expected default kopia cli log cache dir: %v", err)
	}
	if env["HOME"] != "/custom/home" {
		t.Fatalf("HOME overwritten: %q", env["HOME"])
	}
	if env["XDG_CACHE_HOME"] != "/custom/xdg" {
		t.Fatalf("XDG_CACHE_HOME overwritten: %q", env["XDG_CACHE_HOME"])
	}
	if env["LOCALAPPDATA"] != `C:\custom\local` {
		t.Fatalf("LOCALAPPDATA overwritten: %q", env["LOCALAPPDATA"])
	}
	if env["USERPROFILE"] != `C:\custom\profile` {
		t.Fatalf("USERPROFILE overwritten: %q", env["USERPROFILE"])
	}
	if env["KOPIA_USE_KEYRING"] != "true" {
		t.Fatalf("KOPIA_USE_KEYRING overwritten: %q", env["KOPIA_USE_KEYRING"])
	}
	if env["KOPIA_PERSIST_CREDENTIALS_ON_CONNECT"] != "true" {
		t.Fatalf("KOPIA_PERSIST_CREDENTIALS_ON_CONNECT overwritten: %q", env["KOPIA_PERSIST_CREDENTIALS_ON_CONNECT"])
	}
}
