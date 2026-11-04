package enrollmentclient

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/user"
	"strconv"
	"strings"
	"time"

	"hyperfilelens/agent/internal/identity"
	"hyperfilelens/agent/internal/infra/config"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/hostinfo"
	"hyperfilelens/agent/internal/platform/networkinventory"
	"hyperfilelens/agent/internal/selfupdate"
)

// NodeRegistrar persists a control-plane node id after HTTP enrollment.
type NodeRegistrar interface {
	SetNodeID(ctx context.Context, nodeID string) error
}

type nodeCredentialRegistrar interface {
	SetNodeCredential(ctx context.Context, credential string) error
}

type installationIdentityRegistrar interface {
	SetInstallationID(ctx context.Context, installationID string) error
}

// RegistrationResult contains the durable identity returned by enrollment.
type RegistrationResult struct {
	NodeID           string
	NodeCredential   string
	CredentialReused bool
}

const (
	registrationMaxAttempts = 3
	registrationRetryBase   = time.Second
)

// RegistrationRetryCallback receives a retryable registration failure before
// the next attempt starts. attempt is one-based.
type RegistrationRetryCallback func(attempt, maxAttempts int, err error)

// RegistrationRecovery contains only the in-memory authorization and server
// capability needed to recover a registration whose response was lost.
type RegistrationRecovery struct {
	EnrollmentSecret             string
	ClientSessionSecretSupported bool
}

// SessionRenewedCallback records a replacement secret before a recovery POST,
// so cleanup can authenticate even if the response is lost. The secret is
// supplied only in memory.
type SessionRenewedCallback func(secret string)

// EnsureNodeRegistered registers missing identities, migrates legacy credentials,
// and refreshes durable nodes so newly introduced host identity fields are backfilled.
func EnsureNodeRegistered(ctx context.Context, provider config.Provider, reg NodeRegistrar) error {
	if provider == nil {
		return nil
	}
	cfg := provider.Current()
	existingNodeID := strings.TrimSpace(cfg.NodeID)
	if strings.TrimSpace(cfg.InstallationID) == "" {
		generatedID, err := identity.NewInstallationID()
		if err != nil {
			return fmt.Errorf("generate installation identity: %w", err)
		}
		effective := *cfg
		effective.InstallationID = generatedID
		cfg = &effective
		if identityStore, ok := reg.(installationIdentityRegistrar); ok {
			if err := identityStore.SetInstallationID(ctx, generatedID); err != nil {
				return err
			}
		}
	}
	base := strings.TrimRight(strings.TrimSpace(cfg.APIBaseURL), "/")
	org := strings.TrimSpace(cfg.OrgKey)
	token := strings.TrimSpace(cfg.NodeToken)
	if base == "" || org == "" || token == "" {
		return fmt.Errorf("node_id missing; set HFL_NODE_ID or configure HFL_API_BASE, HFL_ORG_KEY, HFL_NODE_TOKEN")
	}

	result, err := httpRegisterNode(
		ctx,
		cfg,
		base,
		org,
		token,
		selfupdate.CurrentBuildIdentity(),
		"",
	)
	if err != nil {
		return err
	}
	if reg == nil {
		return fmt.Errorf("node_id %q from heartbeat but no registrar to persist", result.NodeID)
	}
	if existingNodeID == "" {
		if err := reg.SetNodeID(ctx, result.NodeID); err != nil {
			return err
		}
	} else if result.NodeID != existingNodeID {
		return fmt.Errorf("heartbeat returned node_id %q for configured node_id %q", result.NodeID, existingNodeID)
	}
	if result.NodeCredential != "" {
		if credentialStore, ok := reg.(nodeCredentialRegistrar); ok {
			return credentialStore.SetNodeCredential(ctx, result.NodeCredential)
		}
	}
	return nil
}

// RegisterNodeHTTP registers this host through the enrollment heartbeat.
func RegisterNodeHTTP(
	ctx context.Context,
	cfg *model.AgentConfig,
	build selfupdate.BuildIdentity,
	existingNodeCredential string,
) (RegistrationResult, error) {
	base := strings.TrimRight(strings.TrimSpace(cfg.APIBaseURL), "/")
	org := strings.TrimSpace(cfg.OrgKey)
	token := strings.TrimSpace(cfg.NodeToken)
	if base == "" || org == "" || token == "" {
		return RegistrationResult{}, fmt.Errorf("HFL_API_BASE, HFL_ORG_KEY, and HFL_NODE_TOKEN required")
	}
	build.Version = strings.TrimSpace(build.Version)
	build.Commit = strings.TrimSpace(build.Commit)
	if build.Version == "" || build.Commit == "" {
		return RegistrationResult{}, fmt.Errorf("complete Agent build identity required")
	}
	return httpRegisterNode(
		ctx,
		cfg,
		base,
		org,
		token,
		build,
		existingNodeCredential,
	)
}

// RegisterNodeHTTPWithRetry is the install-time, bounded recovery path. Runtime
// heartbeats continue using RegisterNodeHTTP. Retry the existing session first;
// only an invalid enrollment session AFTER a transient failure may need recovery
// of a committed-but-lost response. Never change the installation identity.
func RegisterNodeHTTPWithRetry(
	ctx context.Context,
	cfg *model.AgentConfig,
	build selfupdate.BuildIdentity,
	existingNodeCredential string,
	recovery RegistrationRecovery,
	onSessionRenewed SessionRenewedCallback,
	onRetry RegistrationRetryCallback,
) (RegistrationResult, error) {
	if cfg.InstallationID == "" || cfg.NodeID != "" || recovery.EnrollmentSecret == "" {
		return RegistrationResult{}, fmt.Errorf("registration retry requires an installation identity and enrollment authorization, without node_id")
	}
	// Preserve the existing 30-second HTTP timeout for a single request:
	// shrinking it would make an otherwise healthy but busy single-worker
	// console fail earlier. Bound the complete recovery path separately.
	retryCtx, cancel := context.WithTimeout(ctx, 90*time.Second)
	defer cancel()
	var lastErr error
	for attempt := 1; attempt <= registrationMaxAttempts; attempt++ {
		result, err := RegisterNodeHTTP(retryCtx, cfg, build, existingNodeCredential)
		if err == nil {
			return result, nil
		}
		if ctx.Err() != nil {
			return RegistrationResult{}, ctx.Err()
		}
		needsRecovery := lastErr != nil && invalidatedRegistrationSession(err)
		if needsRecovery && (!recovery.ClientSessionSecretSupported || attempt == registrationMaxAttempts) {
			// Older consoles cannot safely acknowledge a client-known recovery
			// secret. An exhausted retry budget also cannot recover now.
			// Preserve the transient failure and same-command retry guidance.
			return RegistrationResult{}, lastErr
		}
		if (!IsRetryableRegistrationError(err) && !needsRecovery) || attempt == registrationMaxAttempts {
			return RegistrationResult{}, err
		}
		if !needsRecovery {
			lastErr = err
		}
		if onRetry != nil {
			onRetry(attempt+1, registrationMaxAttempts, err)
		}
		delay := registrationRetryBase * time.Duration(1<<(attempt-1))
		var httpErr *RegistrationError
		if errors.As(err, &httpErr) && httpErr.RetryAfter > delay {
			delay = httpErr.RetryAfter
		}
		timer := time.NewTimer(delay)
		select {
		case <-retryCtx.Done():
			timer.Stop()
			if ctx.Err() != nil {
				return RegistrationResult{}, ctx.Err()
			}
			return RegistrationResult{}, lastErr
		case <-timer.C:
		}
		if needsRecovery {
			if err := renewRegistrationSession(retryCtx, cfg, recovery.EnrollmentSecret, onSessionRenewed); err != nil {
				if ctx.Err() != nil {
					return RegistrationResult{}, ctx.Err()
				}
				return RegistrationResult{}, err
			}
		}
	}
	return RegistrationResult{}, lastErr
}

// First-attempt authorization/mode failures are never retried. These exact
// protocol errors following a transient failure can mean the prior request
// already committed, so its session can no longer return a credential.
func invalidatedRegistrationSession(err error) bool {
	var httpErr *RegistrationError
	if !errors.As(err, &httpErr) {
		return false
	}
	return httpErr.StatusCode == http.StatusUnauthorized && httpErr.Detail == "invalid enrollment token"
}

func renewRegistrationSession(
	ctx context.Context,
	cfg *model.AgentConfig,
	originalEnrollmentSecret string,
	onSessionRenewed SessionRenewedCallback,
) error {
	var random [32]byte
	if _, err := rand.Read(random[:]); err != nil {
		return fmt.Errorf("generate recovery session secret: %w", err)
	}
	secret := "hfls_" + base64.RawURLEncoding.EncodeToString(random[:])
	recoveryCfg := *cfg
	recoveryCfg.NodeToken = originalEnrollmentSecret
	// Record the proposed secret BEFORE the mutating HTTP request. Even if the
	// response is lost, the installer now possesses the secret needed for DELETE.
	cfg.NodeToken = secret
	if onSessionRenewed != nil {
		onSessionRenewed(secret)
	}
	for attempt := 1; attempt <= 2; attempt++ {
		session, err := openInstallationSession(ctx, &recoveryCfg, cfg.InstallationID, secret)
		if err == nil {
			if session.Secret != secret {
				// A mixed-version console might ignore the optional field.
				// Keep its actual reply available for cleanup before stopping.
				cfg.NodeToken = session.Secret
				if onSessionRenewed != nil {
					onSessionRenewed(session.Secret)
				}
				return fmt.Errorf("console did not honor the recovery session secret")
			}
			return nil
		}
		if !IsRetryableRegistrationError(err) || attempt == 2 {
			return err
		}
		timer := time.NewTimer(time.Second)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
	return fmt.Errorf("registration session recovery exhausted")
}

func httpRegisterNode(
	ctx context.Context,
	cfg *model.AgentConfig,
	base, org, token string,
	build selfupdate.BuildIdentity,
	existingNodeCredential string,
) (RegistrationResult, error) {
	hostname, _ := os.Hostname()
	platform := hostinfo.Collect(ctx)
	inventory := platform.Inventory()
	inventory["hostname"] = hostname
	inventory["agent_version"] = build.Version
	inventory["agent_commit"] = build.Commit
	networkSnapshot := networkinventory.Collect(ctx, base)
	if addresses := networkSnapshot.IPAddresses(); len(addresses) > 0 {
		inventory["primary_ip_address"] = networkSnapshot.PrimaryAddress()
		inventory["primary_ip_source"] = networkSnapshot.Selection.Source
		inventory["ip_addresses"] = addresses
		inventory["network_inventory"] = networkSnapshot
	}
	if mac := networkSnapshot.PrimaryMACAddress(); mac != "" {
		inventory["mac_address"] = mac
		inventory["primary_mac_address"] = mac
	}
	metadata := map[string]any{
		"hostname":      hostname,
		"inventory":     inventory,
		"install":       "hfl-enroll",
		"agent_version": build.Version,
		"platform":      platform.OSFamily,
		"arch":          platform.Arch,
	}
	metadata["agent_commit"] = build.Commit
	if currentUser, userErr := user.Current(); userErr == nil {
		metadata["runtime_principal"] = map[string]string{
			"id":   strings.TrimSpace(currentUser.Uid),
			"name": strings.TrimSpace(currentUser.Username),
		}
	}
	body := map[string]any{
		"name":     hostname,
		"role":     string(cfg.Role),
		"version":  build.Version,
		"os_name":  platform.Description(),
		"metadata": metadata,
	}
	machineFingerprint, err := identity.MachineFingerprint(ctx)
	if err != nil {
		return RegistrationResult{}, fmt.Errorf("derive host fingerprint: %w", err)
	}
	if machineFingerprint != "" {
		body["host_fingerprint"] = machineFingerprint
	}
	installationMode := cfg.InstallationMode
	if installationMode == "" {
		installationMode = model.InstallationModeSystem
	}
	body["installation_mode"] = string(installationMode)
	installationID := strings.TrimSpace(cfg.InstallationID)
	if installationID == "" {
		generatedID, identityErr := identity.NewInstallationID()
		if identityErr != nil {
			return RegistrationResult{}, fmt.Errorf("generate installation identity: %w", identityErr)
		}
		installationID = generatedID
	}
	body["installation_id"] = installationID
	if existingNodeCredential = strings.TrimSpace(existingNodeCredential); existingNodeCredential != "" {
		body["existing_node_credential"] = existingNodeCredential
	}
	if id := strings.TrimSpace(cfg.NodeID); id != "" {
		if n, err := strconv.ParseInt(id, 10, 64); err == nil {
			body["node_id"] = n
		}
	}
	payload, err := json.Marshal(body)
	if err != nil {
		return RegistrationResult{}, err
	}

	req, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		base+"/api/v1/node/nodes/heartbeat/",
		bytes.NewReader(payload),
	)
	if err != nil {
		return RegistrationResult{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Org-Key", org)
	req.Header.Set("X-Node-Token", token)

	resp, err := enrollmentHTTPClient(30 * time.Second).Do(req)
	if err != nil {
		return RegistrationResult{}, &RegistrationError{Cause: err}
	}
	defer resp.Body.Close()
	raw, readErr := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return RegistrationResult{}, &RegistrationError{
			StatusCode: resp.StatusCode,
			RetryAfter: registrationRetryAfter(resp.Header.Get("Retry-After")),
			Detail:     registrationErrorDetail(raw),
		}
	}

	if readErr != nil {
		return RegistrationResult{}, &RegistrationError{Cause: readErr}
	}
	data, err := decodeAPIData(raw)
	if err != nil {
		return RegistrationResult{}, fmt.Errorf("heartbeat response: %w", err)
	}
	result := RegistrationResult{}
	if credential, ok := data["node_credential"].(string); ok {
		result.NodeCredential = strings.TrimSpace(credential)
	}
	if reused, ok := data["credential_reused"].(bool); ok {
		result.CredentialReused = reused
	}
	switch value := data["node_id"].(type) {
	case float64:
		result.NodeID = fmt.Sprintf("%.0f", value)
	case json.Number:
		result.NodeID = value.String()
	case string:
		result.NodeID = strings.TrimSpace(value)
	}
	if result.NodeID == "" {
		return RegistrationResult{}, fmt.Errorf("heartbeat missing node_id")
	}
	return result, nil
}

// Bound Retry-After even if a proxy supplies an unreasonable value.
func registrationRetryAfter(value string) time.Duration {
	var delay time.Duration
	if seconds, err := strconv.ParseInt(value, 10, 64); err == nil {
		if seconds <= 0 {
			return 0
		}
		if seconds > 5 {
			return 5 * time.Second
		}
		delay = time.Duration(seconds) * time.Second
	} else if at, err := http.ParseTime(value); err == nil {
		delay = time.Until(at)
	}
	if delay < 0 {
		return 0
	}
	if delay > 5*time.Second {
		return 5 * time.Second
	}
	return delay
}
