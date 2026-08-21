package remote

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"hyperfilelens/agent/internal/model"
)

type credentialRegistrar struct {
	nodeID         string
	credential     string
	installationID string
	nodeIDSetCalls int
}

func (registrar *credentialRegistrar) SetNodeID(_ context.Context, nodeID string) error {
	registrar.nodeID = nodeID
	registrar.nodeIDSetCalls++
	return nil
}

func (registrar *credentialRegistrar) SetNodeCredential(_ context.Context, credential string) error {
	registrar.credential = credential
	return nil
}

func (registrar *credentialRegistrar) SetInstallationID(_ context.Context, installationID string) error {
	registrar.installationID = installationID
	return nil
}

func TestHTTPRegisterNodeIncludesPlatformInventory(t *testing.T) {
	var payload map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/node/nodes/heartbeat/" {
			t.Errorf("unexpected request path %q", r.URL.Path)
		}
		if got := r.Header.Get("X-Org-Key"); got != "test-org" {
			t.Errorf("X-Org-Key=%q", got)
		}
		if got := r.Header.Get("X-Node-Token"); got != "test-token" {
			t.Errorf("X-Node-Token=%q", got)
		}
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Errorf("decode request: %v", err)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"node_id":42}`))
	}))
	defer server.Close()

	cfg := &model.AgentConfig{
		APIBaseURL: server.URL,
		OrgKey:     "test-org",
		NodeToken:  "test-token",
		Role:       model.RoleAgent,
	}
	result, err := RegisterNodeHTTP(context.Background(), cfg, "1.2.3", "")
	if err != nil {
		t.Fatal(err)
	}
	if result.NodeID != "42" {
		t.Fatalf("nodeID=%q", result.NodeID)
	}
	if got := payload["installation_mode"]; got != "system" {
		t.Errorf("installation_mode=%v", got)
	}
	if fingerprint, _ := payload["host_fingerprint"].(string); len(fingerprint) != 64 {
		t.Errorf("host_fingerprint=%q", fingerprint)
	}

	metadata, ok := payload["metadata"].(map[string]any)
	if !ok {
		t.Fatalf("metadata=%T", payload["metadata"])
	}
	inventory, ok := metadata["inventory"].(map[string]any)
	if !ok {
		t.Fatalf("inventory=%T", metadata["inventory"])
	}
	for _, key := range []string{
		"os_family", "os_name", "os_version", "kernel_version", "arch", "service_manager",
	} {
		if _, exists := inventory[key]; !exists {
			t.Errorf("inventory is missing %q", key)
		}
	}
	if got := inventory["agent_version"]; got != "1.2.3" {
		t.Errorf("agent_version=%v", got)
	}
}

func TestEnsureNodeRegisteredMigratesLegacyCredentialForExistingNode(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		if payload["node_id"] != float64(42) {
			t.Fatalf("node_id=%v", payload["node_id"])
		}
		installationID, _ := payload["installation_id"].(string)
		if len(installationID) != 45 || installationID[:5] != "hfli_" {
			t.Fatalf("installation_id=%q", installationID)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"node_id":42,"node_credential":"hfln_replacement"}`))
	}))
	defer server.Close()

	provider := staticProvider{cfg: &model.AgentConfig{
		APIBaseURL: server.URL,
		OrgKey:     "test-org",
		NodeID:     "42",
		NodeToken:  "legacy-token",
		Role:       model.RoleAgent,
	}}
	registrar := &credentialRegistrar{}
	if err := EnsureNodeRegistered(context.Background(), provider, registrar); err != nil {
		t.Fatal(err)
	}
	if registrar.nodeIDSetCalls != 0 {
		t.Fatalf("existing node id was rewritten %d time(s)", registrar.nodeIDSetCalls)
	}
	if registrar.credential != "hfln_replacement" {
		t.Fatalf("credential=%q", registrar.credential)
	}
	if len(registrar.installationID) != 45 || registrar.installationID[:5] != "hfli_" {
		t.Fatalf("persisted installation_id=%q", registrar.installationID)
	}
}

func TestEnsureNodeRegisteredRefreshesDurableCredentialNode(t *testing.T) {
	var payload map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("X-Node-Token"); got != "hfln_durable-credential" {
			t.Errorf("X-Node-Token=%q", got)
		}
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"node_id":42,"credential_reused":true}`))
	}))
	defer server.Close()

	provider := staticProvider{cfg: &model.AgentConfig{
		APIBaseURL:     server.URL,
		OrgKey:         "test-org",
		NodeID:         "42",
		NodeToken:      "hfln_durable-credential",
		InstallationID: "hfli_existing",
		Role:           model.RoleAgent,
	}}
	registrar := &credentialRegistrar{}

	if err := EnsureNodeRegistered(context.Background(), provider, registrar); err != nil {
		t.Fatal(err)
	}
	if registrar.nodeIDSetCalls != 0 || registrar.credential != "" {
		t.Fatalf("durable identity was unexpectedly rewritten: %#v", registrar)
	}
	if payload["node_id"] != float64(42) {
		t.Fatalf("node_id=%v", payload["node_id"])
	}
	if payload["installation_id"] != "hfli_existing" {
		t.Fatalf("installation_id=%v", payload["installation_id"])
	}
	if fingerprint, _ := payload["host_fingerprint"].(string); len(fingerprint) != 64 {
		t.Fatalf("host_fingerprint=%q", fingerprint)
	}
}

func TestRegisterNodeHTTPRequestsExistingCredentialReuse(t *testing.T) {
	var payload map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"node_id":42,"credential_reused":true}`))
	}))
	defer server.Close()

	cfg := &model.AgentConfig{
		APIBaseURL:     server.URL,
		OrgKey:         "test-org",
		NodeToken:      "installation-session",
		InstallationID: "hfli_existing",
		Role:           model.RoleAgent,
	}
	result, err := RegisterNodeHTTP(
		context.Background(),
		cfg,
		"1.2.3",
		"hfln_existing",
	)
	if err != nil {
		t.Fatal(err)
	}
	if !result.CredentialReused || result.NodeCredential != "" {
		t.Fatalf("result=%#v", result)
	}
	if payload["existing_node_credential"] != "hfln_existing" {
		t.Fatalf("existing_node_credential=%v", payload["existing_node_credential"])
	}
}
