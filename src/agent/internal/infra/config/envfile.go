package config

import (
	"bufio"
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"hyperfilelens/agent/internal/platform/atomicfile"
	"hyperfilelens/agent/internal/platform/vfs"
)

const agentEnvFileName = "agent.env"

func agentEnvPath(dataDir string) string {
	return filepath.Join(vfs.AgentConfigDir(dataDir), agentEnvFileName)
}

// ParseEnvFile reads KEY=value lines into a map (does not mutate os.Environ).
func ParseEnvFile(path string) (map[string]string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return map[string]string{}, nil
		}
		return nil, err
	}
	if bytes.HasPrefix(b, []byte{0xEF, 0xBB, 0xBF}) {
		b = b[3:]
	}
	out := map[string]string{}
	sc := bufio.NewScanner(bytes.NewReader(b))
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, val, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		key = strings.TrimSpace(key)
		val = trimEnvQuotes(strings.TrimSpace(val))
		if key != "" {
			out[key] = val
		}
	}
	return out, sc.Err()
}

// WriteEnvFile writes persistent HFL_* keys to agent.env (mode 0600).
func WriteEnvFile(path string, values map[string]string) error {
	if values == nil {
		values = map[string]string{}
	}
	var keys []string
	for _, f := range Registry {
		if !f.Persistent {
			continue
		}
		if _, ok := values[f.Env]; ok {
			keys = append(keys, f.Env)
		}
	}
	sort.Strings(keys)
	seen := map[string]struct{}{}
	var b strings.Builder
	b.WriteString("# HyperFileLens Agent configuration (hot-reloadable)\n")
	b.WriteString("# Edit this file; changes apply on next reload (default ~5s).\n\n")
	for _, env := range keys {
		if _, dup := seen[env]; dup {
			continue
		}
		seen[env] = struct{}{}
		val := values[env]
		if strings.ContainsAny(val, " \t#\"'") {
			val = fmt.Sprintf("%q", val)
		}
		b.WriteString(env)
		b.WriteString("=")
		b.WriteString(val)
		b.WriteString("\n")
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	return atomicfile.Write(path, []byte(b.String()), 0o600)
}

// LoadEnvFile parses KEY=value lines from path and calls os.Setenv only when the key is currently unset.
func LoadEnvFile(path string) error {
	values, err := ParseEnvFile(path)
	if err != nil {
		return err
	}
	for key, val := range values {
		if os.Getenv(key) == "" {
			_ = os.Setenv(key, val)
		}
	}
	return nil
}

func trimEnvQuotes(s string) string {
	if len(s) >= 2 {
		if (s[0] == '"' && s[len(s)-1] == '"') || (s[0] == '\'' && s[len(s)-1] == '\'') {
			return s[1 : len(s)-1]
		}
	}
	return s
}
