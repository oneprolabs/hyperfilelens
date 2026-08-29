package engine

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/vfs"
)

const (
	agentPathForbiddenCode = "AGENT_PATH_FORBIDDEN"
)

var errAgentPathForbidden = errors.New("agent path is protected")

// backupPathBoundary describes the part of the Agent-owned filesystem that a
// host backup must not capture. A NAS source is the only deliberate exception:
// it is a task-bound managed source mount, not Agent state.
type backupPathBoundary struct {
	agentRoot       string
	repositoryRoot  string
	sourceMountRoot string
	customMountRoot string
	ignorePatterns  []string
	excludedPaths   []string
}

func newBackupPathBoundary(cfg *model.AgentConfig, sourcePath string, nasSource bool, nasMountPoints ...string) (backupPathBoundary, error) {
	root := ""
	if cfg != nil {
		root = strings.TrimSpace(cfg.AgentRoot)
		if root == "" {
			root = strings.TrimSpace(cfg.DataDir)
		}
	}
	if root == "" {
		mode := model.InstallationModeSystem
		if cfg != nil && cfg.InstallationMode != "" {
			mode = cfg.InstallationMode
		}
		root = vfs.AgentRootForMode(mode)
	}
	root, err := canonicalPath(root)
	if err != nil {
		return backupPathBoundary{}, fmt.Errorf("resolve Agent root: %w", err)
	}
	b := backupPathBoundary{
		agentRoot:       root,
		repositoryRoot:  vfs.MountRepositoriesDir(root),
		sourceMountRoot: vfs.MountSourcesDir(root),
		customMountRoot: vfs.MountCustomDir(root),
	}
	path, err := canonicalPath(sourcePath)
	if err != nil {
		return backupPathBoundary{}, err
	}
	// A task explicitly bound to a NAS source may read its source mount. The
	// exception is narrow and never permits the repository mount namespace.
	if nasSource && len(nasMountPoints) > 0 && strings.TrimSpace(nasMountPoints[0]) != "" {
		mountRoot, mountErr := canonicalPath(nasMountPoints[0])
		if mountErr == nil {
			isManagedSourceMount := isWithin(mountRoot, b.sourceMountRoot) || isWithin(mountRoot, b.customMountRoot)
			if isManagedSourceMount && !isWithin(mountRoot, b.repositoryRoot) && isWithin(path, mountRoot) {
				return b, nil
			}
		}
	}
	if isWithin(path, b.agentRoot) {
		return backupPathBoundary{}, fmt.Errorf(
			"%w: backup source must not be inside Agent root %q",
			errAgentPathForbidden,
			root,
		)
	}
	systemPatterns, systemExclusions, forbiddenSystemPath, systemErr := systemBackupBoundaryRules(b.agentRoot, path)
	if systemErr != nil {
		return backupPathBoundary{}, systemErr
	}
	if forbiddenSystemPath != "" {
		return backupPathBoundary{}, fmt.Errorf(
			"%w: backup source must not be inside protected system path %q",
			errAgentPathForbidden,
			forbiddenSystemPath,
		)
	}
	b.ignorePatterns = append(b.ignorePatterns, systemPatterns...)
	b.excludedPaths = append(b.excludedPaths, systemExclusions...)
	// When backing up a parent (for example / or a user's home), exclude the
	// complete Agent root. This keeps all internal subdirectories protected,
	// including mounts/repositories and future layout additions.
	if isWithin(b.agentRoot, path) {
		rel, relErr := filepath.Rel(path, b.agentRoot)
		if relErr != nil || rel == "." || rel == ".." ||
			strings.HasPrefix(rel, ".."+string(filepath.Separator)) ||
			filepath.IsAbs(rel) {
			return backupPathBoundary{}, fmt.Errorf(
				"%w: invalid Agent root boundary",
				errAgentPathForbidden,
			)
		}
		// Kopia excludes the directory entry and its entire subtree when the
		// directory path itself is ignored. A trailing /** leaves an empty Agent
		// root entry in the snapshot and is therefore intentionally not used.
		b.ignorePatterns = append(b.ignorePatterns, normalizeIgnorePattern(rel))
		b.excludedPaths = append(b.excludedPaths, b.agentRoot)
	}
	return b, nil
}

func canonicalPath(path string) (string, error) {
	clean := strings.TrimSpace(path)
	if clean == "" {
		return "", os.ErrInvalid
	}
	abs, err := filepath.Abs(filepath.Clean(clean))
	if err != nil {
		return "", err
	}
	// Eval existing symlinks so a symlink into AgentRoot cannot bypass the
	// boundary. The leaf may not exist yet; resolve its nearest existing parent
	// and append the non-existing suffix.
	for candidate := abs; ; candidate = filepath.Dir(candidate) {
		if resolved, evalErr := filepath.EvalSymlinks(candidate); evalErr == nil {
			rel, relErr := filepath.Rel(candidate, abs)
			if relErr != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
				return filepath.Clean(abs), nil
			}
			return filepath.Clean(filepath.Join(resolved, rel)), nil
		}
		parent := filepath.Dir(candidate)
		if parent == candidate {
			break
		}
	}
	return filepath.Clean(abs), nil
}

func isWithin(path, root string) bool {
	path = filepath.Clean(path)
	root = filepath.Clean(root)
	if runtime.GOOS == "windows" {
		path = strings.ToLower(path)
		root = strings.ToLower(root)
	}
	if path == root {
		return true
	}
	rel, err := filepath.Rel(root, path)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel)
}

func normalizeIgnorePattern(rel string) string {
	return strings.Trim(strings.ReplaceAll(filepath.ToSlash(filepath.Clean(rel)), "\\", "/"), "/")
}

func (b backupPathBoundary) patterns() []string { return append([]string(nil), b.ignorePatterns...) }

func (b backupPathBoundary) exclusions() []string { return append([]string(nil), b.excludedPaths...) }

func agentPathBoundaryErrorResult(err error) map[string]any {
	if !errors.Is(err, errAgentPathForbidden) {
		return nil
	}
	return map[string]any{"error_code": agentPathForbiddenCode}
}
