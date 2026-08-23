package config

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"hyperfilelens/agent/internal/platform/atomicfile"
)

var retiredInstallationEnvKeys = map[string]struct{}{
	"HFL_INSTALLATION_ID": {},
	"HFL_NODE_CREDENTIAL": {},
	"HFL_NODE_ID":         {},
	"HFL_NODE_TOKEN":      {},
}

var retiredInstallationJSONKeys = []string{
	"installation_id",
	"node_id",
	"node_token",
}

// ClearNodeTokenJSONOverride removes a stale credential override from config.json.
// The durable credential remains in agent.env, which is the authoritative store
// for credentials issued during enrollment.
func ClearNodeTokenJSONOverride(dataDir string) error {
	dataDir = strings.TrimSpace(dataDir)
	if dataDir == "" {
		return fmt.Errorf("data directory is required")
	}
	return clearNodeTokenJSONOverridePath(jsonConfigPath(dataDir))
}

func clearNodeTokenJSONOverridePath(path string) error {
	update, err := readIdentityFile(path)
	if err != nil || !update.exists {
		return err
	}
	values := map[string]json.RawMessage{}
	if err := json.Unmarshal(update.original, &values); err != nil {
		return fmt.Errorf("parse %s: %w", path, err)
	}
	if _, present := values["node_token"]; !present {
		return nil
	}
	delete(values, "node_token")
	updated, err := json.MarshalIndent(values, "", "  ")
	if err != nil {
		return fmt.Errorf("encode %s: %w", path, err)
	}
	return atomicfile.Write(path, append(updated, '\n'), 0o600)
}

type identityFileUpdate struct {
	path     string
	original []byte
	updated  []byte
	exists   bool
	changed  bool
}

// RetireInstallationIdentity removes control-plane identity while preserving data.
func RetireInstallationIdentity(dataDir string) error {
	dataDir = strings.TrimSpace(dataDir)
	if dataDir == "" {
		return fmt.Errorf("data directory is required")
	}
	envUpdate, err := prepareEnvIdentityRetirement(agentEnvPath(dataDir))
	if err != nil {
		return err
	}
	jsonUpdate, err := prepareJSONIdentityRetirement(jsonConfigPath(dataDir))
	if err != nil {
		return err
	}

	written := []identityFileUpdate{}
	for _, update := range []identityFileUpdate{envUpdate, jsonUpdate} {
		if !update.exists || !update.changed {
			continue
		}
		if err := atomicfile.Write(update.path, update.updated, 0o600); err != nil {
			for index := len(written) - 1; index >= 0; index-- {
				previous := written[index]
				if restoreErr := atomicfile.Write(
					previous.path,
					previous.original,
					0o600,
				); restoreErr != nil {
					return fmt.Errorf(
						"retire installation identity: %w; restore %s: %v",
						err,
						previous.path,
						restoreErr,
					)
				}
			}
			return fmt.Errorf("retire installation identity in %s: %w", update.path, err)
		}
		written = append(written, update)
	}
	return nil
}

func prepareEnvIdentityRetirement(path string) (identityFileUpdate, error) {
	update, err := readIdentityFile(path)
	if err != nil || !update.exists {
		return update, err
	}
	lines := bytes.Split(update.original, []byte("\n"))
	kept := make([][]byte, 0, len(lines))
	for _, line := range lines {
		trimmed := strings.TrimSpace(string(line))
		key, _, found := strings.Cut(trimmed, "=")
		if found {
			if _, retire := retiredInstallationEnvKeys[strings.TrimSpace(key)]; retire {
				continue
			}
		}
		kept = append(kept, line)
	}
	update.updated = bytes.Join(kept, []byte("\n"))
	update.changed = !bytes.Equal(update.original, update.updated)
	return update, nil
}

func prepareJSONIdentityRetirement(path string) (identityFileUpdate, error) {
	update, err := readIdentityFile(path)
	if err != nil || !update.exists {
		return update, err
	}
	values := map[string]json.RawMessage{}
	if err := json.Unmarshal(update.original, &values); err != nil {
		return identityFileUpdate{}, fmt.Errorf("parse %s: %w", path, err)
	}
	removed := false
	for _, key := range retiredInstallationJSONKeys {
		if _, present := values[key]; present {
			delete(values, key)
			removed = true
		}
	}
	if !removed {
		update.updated = update.original
		return update, nil
	}
	updated, err := json.MarshalIndent(values, "", "  ")
	if err != nil {
		return identityFileUpdate{}, fmt.Errorf("encode %s: %w", path, err)
	}
	update.updated = append(updated, '\n')
	update.changed = !bytes.Equal(update.original, update.updated)
	return update, nil
}

func readIdentityFile(path string) (identityFileUpdate, error) {
	path = filepath.Clean(path)
	content, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return identityFileUpdate{path: path}, nil
	}
	if err != nil {
		return identityFileUpdate{}, fmt.Errorf("read %s: %w", path, err)
	}
	return identityFileUpdate{
		path:     path,
		original: content,
		exists:   true,
	}, nil
}
