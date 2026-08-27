package remote

import (
	"context"

	"hyperfilelens/agent/internal/enrollmentclient"
	"hyperfilelens/agent/internal/infra/config"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/selfupdate"
)

// NodeRegistrar persists a control-plane node id after HTTP enrollment.
type NodeRegistrar = enrollmentclient.NodeRegistrar

// RegistrationResult contains the durable identity returned by enrollment.
type RegistrationResult = enrollmentclient.RegistrationResult

// EnsureNodeRegistered registers missing identities and migrates legacy credentials.
func EnsureNodeRegistered(ctx context.Context, provider config.Provider, registrar NodeRegistrar) error {
	return enrollmentclient.EnsureNodeRegistered(ctx, provider, registrar)
}

// RegisterNodeHTTP registers this host through the enrollment heartbeat.
func RegisterNodeHTTP(
	ctx context.Context,
	cfg *model.AgentConfig,
	build selfupdate.BuildIdentity,
	existingNodeCredential string,
) (RegistrationResult, error) {
	return enrollmentclient.RegisterNodeHTTP(
		ctx,
		cfg,
		build,
		existingNodeCredential,
	)
}
