package config

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"hyperfilelens/agent/internal/model"
)

const defaultReloadInterval = 5 * time.Second

// Store holds the hot-reloadable effective configuration.
// Merge order on each reload: process env at bootstrap → agent.env → config.json → startup CLI overrides.
type Store struct {
	snapshot atomic.Pointer[model.AgentConfig]

	mu               sync.Mutex
	base             *model.AgentConfig
	overrides        Overrides
	envPath          string
	jsonPath         string
	installationMode model.InstallationMode
	dataDir          string
	role             model.Role
	identityLocked   bool
	lastEnvMod       time.Time
	lastJSONMod      time.Time
}

// NewStore builds the initial snapshot and persists agent.env when missing.
func NewStore(ctx context.Context, opts LoadOptions) (*Store, error) {
	_ = ctx
	if err := BootstrapAgentHome(); err != nil {
		return nil, err
	}

	dataDir, err := ResolveDataDir(opts.Overrides)
	if err != nil {
		return nil, err
	}

	base, err := RuntimeFromEnv()
	if err != nil {
		return nil, err
	}
	s := &Store{
		base:      base,
		overrides: opts.Overrides,
		envPath:   agentEnvPath(dataDir),
		jsonPath:  jsonConfigPath(dataDir),
	}
	if err := s.ensurePersisted(); err != nil {
		return nil, err
	}
	if err := s.reloadLocked(); err != nil {
		return nil, err
	}
	return s, nil
}

func (s *Store) ensurePersisted() error {
	_, err := os.Stat(s.envPath)
	if err == nil {
		return nil
	}
	if !os.IsNotExist(err) {
		return err
	}
	cfg := cloneConfig(s.base)
	applyOverrides(cfg, s.overrides)
	return WriteEnvFile(s.envPath, configToEnvMap(cfg))
}

func (s *Store) reloadLocked() error {
	cfg := cloneConfig(s.base)

	if values, err := ParseEnvFile(s.envPath); err != nil {
		return err
	} else {
		if err := applyEnvMap(cfg, values); err != nil {
			return err
		}
	}
	if fi, err := os.Stat(s.envPath); err == nil {
		s.lastEnvMod = fi.ModTime()
	}

	if overlay, err := readJSONOverlay(s.jsonPath); err != nil {
		return err
	} else {
		if err := applyEnvMap(cfg, overlayToEnvMap(overlay)); err != nil {
			return err
		}
	}
	if fi, err := os.Stat(s.jsonPath); err == nil {
		s.lastJSONMod = fi.ModTime()
	} else if os.IsNotExist(err) {
		s.lastJSONMod = time.Time{}
	}

	applyOverrides(cfg, s.overrides)
	if !s.identityLocked {
		s.installationMode = installationModeForExecutable(cfg.InstallationMode)
		s.dataDir = strings.TrimSpace(cfg.DataDir)
		s.role = cfg.Role
		s.identityLocked = true
	}
	if s.installationMode == model.InstallationModeUser &&
		s.role != "" && s.role != model.RoleAgent {
		return fmt.Errorf("user-level installation is only available for Source Agent")
	}
	cfg.InstallationMode = s.installationMode
	cfg.DataDir = s.dataDir
	cfg.Role = s.role
	s.snapshot.Store(cfg)
	return nil
}

// Reload re-reads agent.env and config.json; effective on the next Current() use.
func (s *Store) Reload(ctx context.Context) error {
	_ = ctx
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.reloadLocked()
}

// Current returns the latest configuration snapshot.
func (s *Store) Current() *model.AgentConfig {
	if v := s.snapshot.Load(); v != nil {
		return v
	}
	return &model.AgentConfig{}
}

// Paths exposes persisted config file locations.
func (s *Store) Paths() (envFile, jsonFile string) {
	return s.envPath, s.jsonPath
}

// SetEnv updates one HFL_* value in agent.env and reloads the snapshot.
func (s *Store) SetEnv(ctx context.Context, envKey, value string) error {
	envKey = strings.TrimSpace(envKey)
	if envKey == "" {
		return os.ErrInvalid
	}
	f, ok := fieldByEnv(envKey)
	if !ok || !f.Persistent {
		return os.ErrInvalid
	}
	if f.ReadOnly {
		return fmt.Errorf("%s is installer-owned and cannot be changed at runtime", envKey)
	}
	s.mu.Lock()
	defer s.mu.Unlock()

	values, err := ParseEnvFile(s.envPath)
	if err != nil {
		return err
	}
	if strings.TrimSpace(value) == "" {
		delete(values, envKey)
	} else {
		values[envKey] = strings.TrimSpace(value)
	}
	if err := WriteEnvFile(s.envPath, values); err != nil {
		return err
	}
	return s.reloadLocked()
}

// SaveSnapshot writes the current effective config back to agent.env and config.json.
func (s *Store) SaveSnapshot(ctx context.Context) error {
	_ = ctx
	s.mu.Lock()
	defer s.mu.Unlock()
	cfg := cloneConfig(s.Current())
	values := configToEnvMap(cfg)
	if err := WriteEnvFile(s.envPath, values); err != nil {
		return err
	}
	return writeJSONOverlay(s.jsonPath, envMapToOverlay(values))
}

// SetNodeID persists the control-plane node id to agent.env.
func (s *Store) SetNodeID(ctx context.Context, nodeID string) error {
	return s.SetEnv(ctx, "HFL_NODE_ID", strings.TrimSpace(nodeID))
}

// SetInstallationID persists the identity for the current installation lifetime.
func (s *Store) SetInstallationID(ctx context.Context, installationID string) error {
	return s.SetEnv(ctx, "HFL_INSTALLATION_ID", strings.TrimSpace(installationID))
}

// SetNodeCredential replaces temporary enrollment material with a node credential.
func (s *Store) SetNodeCredential(_ context.Context, credential string) error {
	credential = strings.TrimSpace(credential)
	if credential == "" {
		return os.ErrInvalid
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := ClearNodeTokenJSONOverride(filepath.Dir(s.jsonPath)); err != nil {
		return err
	}
	values, err := ParseEnvFile(s.envPath)
	if err != nil {
		return err
	}
	delete(values, "HFL_NODE_TOKEN")
	values["HFL_NODE_CREDENTIAL"] = credential
	if err := WriteEnvFile(s.envPath, values); err != nil {
		return err
	}
	return s.reloadLocked()
}

// Watch polls config files and reloads when they change.
func (s *Store) Watch(ctx context.Context, interval time.Duration) {
	if interval <= 0 {
		interval = defaultReloadInterval
	}
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if !s.filesChanged() {
				continue
			}
			if err := s.Reload(ctx); err != nil {
				slog.Warn("config reload failed", "err", err)
				continue
			}
			cfg := s.Current()
			slog.Info("config reloaded",
				"wss", cfg.WSSURL,
				"role", cfg.Role,
				"env_file", s.envPath,
			)
		}
	}
}

func (s *Store) filesChanged() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	changed := false
	if fi, err := os.Stat(s.envPath); err == nil {
		if fi.ModTime().After(s.lastEnvMod) {
			changed = true
		}
	}
	if fi, err := os.Stat(s.jsonPath); err == nil {
		if fi.ModTime().After(s.lastJSONMod) {
			changed = true
		}
	} else if os.IsNotExist(err) && !s.lastJSONMod.IsZero() {
		changed = true
	}
	return changed
}

// Ensure Store implements Provider.
var _ Provider = (*Store)(nil)
