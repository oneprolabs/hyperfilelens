package engine

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"hyperfilelens/agent/internal/platform/vfs"
)

const systemBackupBoundaryConfigName = "system-backup-boundaries.json"

type systemBackupBoundaryConfig struct {
	SchemaVersion   int      `json:"schema_version"`
	AdditionalPaths []string `json:"additional_paths"`
}

type systemBackupBoundaryCandidate struct {
	path            string
	directory       bool
	caseInsensitive bool
}

// systemBackupBoundaryCandidates returns only operating-system runtime paths
// that cannot be restored as ordinary user data. General caches, logs,
// temporary directories, and user-owned paths are intentionally not included.
func systemBackupBoundaryCandidates(sourcePath string) []systemBackupBoundaryCandidate {
	switch runtime.GOOS {
	case "linux":
		return []systemBackupBoundaryCandidate{
			{path: "/proc", directory: true},
			{path: "/sys", directory: true},
			{path: "/dev", directory: true},
			{path: "/run", directory: true},
		}
	case "darwin":
		return []systemBackupBoundaryCandidate{
			{path: "/.vol", directory: true},
			{path: "/dev", directory: true},
			{path: "/private/var/run", directory: true},
			{path: "/private/var/vm", directory: true},
			{path: "/System/Volumes/VM", directory: true},
		}
	case "windows":
		volume := filepath.VolumeName(sourcePath)
		if volume == "" {
			return nil
		}
		root := filepath.Clean(volume + string(filepath.Separator))
		return windowsSystemBackupBoundaryCandidates(root)
	default:
		return nil
	}
}

func windowsSystemBackupBoundaryCandidates(root string) []systemBackupBoundaryCandidate {
	return []systemBackupBoundaryCandidate{
		{path: filepath.Join(root, "System Volume Information"), directory: true, caseInsensitive: true},
		{path: filepath.Join(root, "DumpStack.log.tmp"), caseInsensitive: true},
		{path: filepath.Join(root, "pagefile.sys"), caseInsensitive: true},
		{path: filepath.Join(root, "hiberfil.sys"), caseInsensitive: true},
		{path: filepath.Join(root, "swapfile.sys"), caseInsensitive: true},
	}
}

func loadAdditionalSystemBackupBoundaryPaths(agentRoot string) []systemBackupBoundaryCandidate {
	configPath := filepath.Join(vfs.AgentConfigDir(agentRoot), systemBackupBoundaryConfigName)
	data, err := os.ReadFile(configPath)
	if err != nil {
		if !os.IsNotExist(err) {
			slog.Warn("system backup boundary config unavailable", "path", configPath, "err", err)
		}
		return nil
	}

	var cfg systemBackupBoundaryConfig
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&cfg); err != nil {
		slog.Warn("system backup boundary config invalid; using built-in rules", "path", configPath, "err", err)
		return nil
	}
	if cfg.SchemaVersion != 1 {
		slog.Warn("system backup boundary config version unsupported; using built-in rules", "path", configPath, "schema_version", cfg.SchemaVersion)
		return nil
	}

	result := make([]systemBackupBoundaryCandidate, 0, len(cfg.AdditionalPaths))
	for _, rawPath := range cfg.AdditionalPaths {
		path := strings.TrimSpace(rawPath)
		if path == "" || !filepath.IsAbs(path) {
			slog.Warn("system backup boundary config path ignored; path must be absolute", "path", rawPath)
			continue
		}
		result = append(result, systemBackupBoundaryCandidate{path: path, directory: true})
	}
	return result
}

func rootedSystemIgnorePattern(sourcePath, protectedPath string, directory, caseInsensitive bool) (string, error) {
	rel, err := filepath.Rel(sourcePath, protectedPath)
	if err != nil || rel == "." || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || filepath.IsAbs(rel) {
		return "", fmt.Errorf("protected path %q is not below source %q", protectedPath, sourcePath)
	}
	pattern := "/" + strings.Trim(filepath.ToSlash(filepath.Clean(rel)), "/")
	if caseInsensitive {
		pattern = caseFoldSystemIgnorePattern(pattern)
	}
	if directory {
		pattern += "/"
	}
	return pattern, nil
}

func caseFoldSystemIgnorePattern(pattern string) string {
	var result strings.Builder
	for _, ch := range pattern {
		switch {
		case ch >= 'a' && ch <= 'z':
			result.WriteByte('[')
			result.WriteByte(byte(ch - ('a' - 'A')))
			result.WriteByte(byte(ch))
			result.WriteByte(']')
		case ch >= 'A' && ch <= 'Z':
			result.WriteByte('[')
			result.WriteByte(byte(ch))
			result.WriteByte(byte(ch + ('a' - 'A')))
			result.WriteByte(']')
		default:
			result.WriteRune(ch)
		}
	}
	return result.String()
}

func systemBackupBoundaryRules(agentRoot, sourcePath string) (patterns, exclusions []string, forbiddenPath string, err error) {
	candidates := append(systemBackupBoundaryCandidates(sourcePath), loadAdditionalSystemBackupBoundaryPaths(agentRoot)...)
	seen := make(map[string]struct{}, len(candidates))
	for _, candidate := range candidates {
		protectedPath, canonicalErr := canonicalPath(candidate.path)
		if canonicalErr != nil {
			return nil, nil, "", fmt.Errorf("resolve system backup boundary %q: %w", candidate.path, canonicalErr)
		}
		key := protectedPath
		if runtime.GOOS == "windows" {
			key = strings.ToLower(key)
		}
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}

		if isWithin(sourcePath, protectedPath) {
			return nil, nil, protectedPath, nil
		}
		if !isWithin(protectedPath, sourcePath) {
			continue
		}
		pattern, patternErr := rootedSystemIgnorePattern(sourcePath, protectedPath, candidate.directory, candidate.caseInsensitive)
		if patternErr != nil {
			return nil, nil, "", patternErr
		}
		patterns = append(patterns, pattern)
		exclusions = append(exclusions, protectedPath)
	}
	return patterns, exclusions, "", nil
}
