package enroll

import (
	"context"

	"hyperfilelens/agent/internal/identity"
)

func installationID(_ context.Context, cfg Config) (string, error) {
	return resolveInstallationID(installedEnvPath(), cfg)
}

func resolveInstallationID(envPath string, cfg Config) (string, error) {
	if persisted := readEnvKey(envPath, "HFL_INSTALLATION_ID"); persisted != "" {
		return persisted, nil
	}
	if cfg.InstallationID != "" {
		return cfg.InstallationID, nil
	}
	return identity.NewInstallationID()
}

func persistInstallationID(installationID string) error {
	return WriteInstallationID(EnvFilePath(), installationID)
}
