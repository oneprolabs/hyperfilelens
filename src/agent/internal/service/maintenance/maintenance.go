package maintenance

import (
	"context"
	"fmt"
	"path/filepath"

	"hyperfilelens/agent/internal/infra/kopialayout"
	"hyperfilelens/agent/internal/platform/kopia"
	"hyperfilelens/agent/internal/platform/process"
	"hyperfilelens/agent/internal/platform/vfs"
)

// Service performs repository maintenance and local housekeeping.
type Service struct {
	binary string
}

// NewService returns a maintenance service using the resolved Kopia binary path.
func NewService(kopiaPath string) (*Service, error) {
	bin, err := kopia.Resolve(kopiaPath)
	if err != nil {
		return nil, err
	}
	return &Service{binary: bin}, nil
}

// RunGC runs Kopia's full repository maintenance command.
func (s *Service) RunGC(ctx context.Context, configFile string) (process.Result, error) {
	root := vfs.DefaultAgentDataDir()
	configFile = kopialayout.ResolveConfig(root, configFile)
	if configFile == "" || configFile == "." {
		return process.Result{}, fmt.Errorf("an explicit Kopia configuration is required")
	}
	if err := kopialayout.PrepareConfigDir(root, configFile); err != nil {
		return process.Result{}, err
	}
	release, err := kopialayout.AcquireUsage(ctx, root)
	if err != nil {
		return process.Result{}, err
	}
	defer release()
	env, err := kopialayout.ProcessEnv(root, vfs.AgentLogDir(root), map[string]string{
		"KOPIA_CACHE_DIRECTORY": kopialayout.CacheDir(root, configFile),
	})
	if err != nil {
		return process.Result{}, err
	}
	args := []string{"maintenance", "run", "--full"}
	if configFile != "" {
		args = append([]string{"--config-file=" + configFile}, args...)
	}
	return process.Run(ctx, s.binary, args, env, "")
}

// RotateLogs is a placeholder for local log rotation (see infra/logging).
func (s *Service) RotateLogs(ctx context.Context, logDir string) error {
	_ = s
	_ = ctx
	_ = logDir
	return nil
}

// CleanCache accepts only an installation's verified disposable cache root.
func (s *Service) CleanCache(ctx context.Context, cacheDir string) error {
	if cacheDir == "" {
		return nil
	}
	if !filepath.IsAbs(cacheDir) || filepath.Base(filepath.Clean(cacheDir)) != "cache" {
		return fmt.Errorf("cache cleanup requires the managed installation cache root")
	}
	return kopialayout.ClearCache(ctx, filepath.Dir(filepath.Clean(cacheDir)))
}
