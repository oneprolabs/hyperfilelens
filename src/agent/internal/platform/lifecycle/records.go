// Package lifecycle owns private, crash-safe local upgrade evidence. Records are
// not control-plane success: session/build verification still belongs to the host.
package lifecycle

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"hyperfilelens/agent/internal/platform/atomicfile"
)

// ResolveAgentRoot normalizes the trusted root supplied by configuration or
// the installer. It is not for paths below that root or private workspaces;
// those must still pass Path or VerificationPath without following symlinks.
func ResolveAgentRoot(root string) (string, error) {
	if root == "" {
		return "", fmt.Errorf("missing configured Agent Root")
	}
	abs, err := filepath.Abs(root)
	if err != nil {
		return "", err
	}
	resolved, err := filepath.EvalSymlinks(abs)
	if err != nil {
		return "", fmt.Errorf("resolve configured Agent Root: %w", err)
	}
	return resolved, nil
}

// Path rejects aliases and writable-by-others ancestors inside the Agent Root.
// The root is installation-owned; paths outside it are never accepted.
func Path(root, relative string) (string, error) {
	return pathFor(root, relative, -1)
}

// VerificationPath is only for read-only administrator recovery of a configured
// account installation. It does not change ownership or runtime/write policy.
func VerificationPath(root, relative string, runtimeUID int64) (string, error) {
	if runtimeUID != -1 && !CanVerifyRuntimeOwner(runtimeUID) {
		return "", fmt.Errorf("invalid administrator verification owner")
	}
	return pathFor(root, relative, runtimeUID)
}

func pathFor(root, relative string, auditUID int64) (string, error) {
	if !filepath.IsAbs(root) || filepath.Clean(root) != root || filepath.IsAbs(relative) {
		return "", fmt.Errorf("invalid lifecycle root or relative path")
	}
	path := filepath.Join(root, relative)
	rel, err := filepath.Rel(root, path)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("lifecycle path escapes root")
	}
	current := root
	parts := append([]string{""}, strings.Split(rel, string(filepath.Separator))...)
	for i, part := range parts {
		current = filepath.Join(current, part)
		info, err := os.Lstat(current)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return "", err
		}
		if info.Mode()&os.ModeSymlink != 0 || (!info.IsDir() && !info.Mode().IsRegular()) {
			return "", fmt.Errorf("unsafe lifecycle path")
		}
		// Account installations keep the top-level directory with the
		// administrator. Runtime subdirectories/records still belong to the
		// service account, with no group or world write permission.
		if err := checkOwner(info, i == 0 && info.IsDir(), auditUID); err != nil {
			return "", err
		}
	}
	return path, nil
}

func Write(root, relative string, value any) error {
	path, err := Path(root, relative)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	if _, err := Path(root, relative); err != nil {
		return err
	}
	raw, err := json.Marshal(value)
	if err != nil {
		return err
	}
	if err := atomicfile.Write(path, raw, 0600); err != nil {
		return err
	}
	return syncDirectory(filepath.Dir(path))
}

func Read(root, relative string, value any) error {
	path, err := Path(root, relative)
	if err != nil {
		return err
	}
	return readRecord(path, value, false)
}

func ReadForVerification(root, relative string, uid int64, value any) error {
	path, err := VerificationPath(root, relative, uid)
	if err != nil {
		return err
	}
	return readRecord(path, value, false)
}

// ReadUpgradePhase reads installer-owned state. Windows PowerShell 5.1 used
// UTF-8 with a BOM for these records; Agent-written records stay strict JSON.
func ReadUpgradePhase(root string) (string, error) {
	root, err := ResolveAgentRoot(root)
	if err != nil {
		return "", err
	}
	path, err := Path(root, "lifecycle/upgrade-state.json")
	if err != nil {
		return "", err
	}
	var state struct {
		Phase string `json:"phase"`
	}
	if err := readRecord(path, &state, true); err != nil {
		return "", err
	}
	if strings.TrimSpace(state.Phase) == "" {
		return "", fmt.Errorf("upgrade state has no phase")
	}
	return state.Phase, nil
}

func readRecord(path string, value any, allowBOM bool) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	raw, err := io.ReadAll(io.LimitReader(f, (1<<20)+1))
	if err != nil {
		return err
	}
	if len(raw) > 1<<20 {
		return fmt.Errorf("lifecycle record too large")
	}
	if allowBOM {
		raw = bytes.TrimPrefix(raw, []byte("\xef\xbb\xbf"))
	}
	return json.Unmarshal(raw, value)
}
