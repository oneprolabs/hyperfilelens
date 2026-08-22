package vfs

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// UserHome returns the canonical Home directory used by a user-level Agent.
func UserHome() (string, error) {
	_, canonical, err := userHomePaths()
	return canonical, err
}

func userHomePaths() (declared, canonical string, err error) {
	home := strings.TrimSpace(os.Getenv("HFL_RUN_AS_HOME"))
	if home == "" {
		home, err = os.UserHomeDir()
	}
	if err != nil || strings.TrimSpace(home) == "" {
		return "", "", fmt.Errorf("current user Home directory is unavailable")
	}
	home, err = filepath.Abs(home)
	if err != nil {
		return "", "", fmt.Errorf("resolve current user Home directory: %w", err)
	}
	home = filepath.Clean(home)
	resolved, err := filepath.EvalSymlinks(home)
	if err != nil {
		return "", "", fmt.Errorf("resolve current user Home directory links: %w", err)
	}
	return home, filepath.Clean(resolved), nil
}

// ResolveUserScopedPath applies the platform boundary for a user-scoped Agent.
// Current-user mode remains under Home. Specified-user continuous mode uses the
// selected account's process permissions. Windows paths may use readable local
// fixed drives. Missing final components are permitted for restore destinations
// when requested.
func ResolveUserScopedPath(path string, allowMissing bool) (string, error) {
	return resolveUserScopedPath(path, allowMissing)
}

func resolveHomeScopedPath(path string, allowMissing bool) (string, error) {
	declaredHome, home, err := userHomePaths()
	if err != nil {
		return "", err
	}
	path = strings.TrimSpace(path)
	if path == "" {
		return home, nil
	}
	if !filepath.IsAbs(path) {
		path = filepath.Join(declaredHome, path)
	}
	absPath, err := filepath.Abs(path)
	if err != nil {
		return "", fmt.Errorf("resolve path: %w", err)
	}
	absPath = filepath.Clean(absPath)
	resolved := absPath
	if allowMissing {
		resolved, err = resolvePathWithMissingTail(absPath)
	} else {
		resolved, err = filepath.EvalSymlinks(absPath)
	}
	if err != nil {
		return "", err
	}
	resolved = filepath.Clean(resolved)
	if !pathWithinHome(resolved, home) {
		return "", fmt.Errorf(
			"%w: user-level Agent paths must remain under %s",
			os.ErrPermission,
			home,
		)
	}
	return resolved, nil
}

// resolveAccountScopedPath relies on the service's selected account token for
// Unix access checks. An account may legitimately own service data outside its
// profile directory, so account mode does not impose a Home-only boundary.
func resolveAccountScopedPath(path string, allowMissing bool) (string, error) {
	declaredHome, _, err := userHomePaths()
	if err != nil {
		return "", err
	}
	path = strings.TrimSpace(path)
	if path == "" {
		return declaredHome, nil
	}
	if !filepath.IsAbs(path) {
		path = filepath.Join(declaredHome, path)
	}
	absPath, err := filepath.Abs(path)
	if err != nil {
		return "", fmt.Errorf("resolve path: %w", err)
	}
	absPath = filepath.Clean(absPath)
	resolved := absPath
	if allowMissing {
		resolved, err = resolvePathWithMissingTail(absPath)
	} else {
		resolved, err = filepath.EvalSymlinks(absPath)
	}
	if err != nil {
		return "", err
	}
	resolved = filepath.Clean(resolved)
	probe := resolved
	if allowMissing {
		probe = filepath.Dir(resolved)
	}
	handle, err := os.Open(probe)
	if err != nil {
		return "", fmt.Errorf("%w: specified account cannot read %s", os.ErrPermission, resolved)
	}
	_ = handle.Close()
	return resolved, nil
}

func resolvePathWithMissingTail(path string) (string, error) {
	existing := path
	missing := make([]string, 0, 4)
	for {
		_, err := os.Lstat(existing)
		if err == nil {
			break
		}
		if !os.IsNotExist(err) {
			return "", err
		}
		parent := filepath.Dir(existing)
		if parent == existing {
			return "", err
		}
		missing = append(missing, filepath.Base(existing))
		existing = parent
	}
	resolved, err := filepath.EvalSymlinks(existing)
	if err != nil {
		return "", err
	}
	for index := len(missing) - 1; index >= 0; index-- {
		resolved = filepath.Join(resolved, missing[index])
	}
	return resolved, nil
}
