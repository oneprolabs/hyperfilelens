package enrollmentclient

import (
	"bytes"
	"context"
	"encoding/json"
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
		return RegistrationResult{}, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return RegistrationResult{}, fmt.Errorf("heartbeat HTTP %s: %s", resp.Status, strings.TrimSpace(string(raw)))
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
