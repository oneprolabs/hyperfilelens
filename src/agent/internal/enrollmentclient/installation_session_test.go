package enrollmentclient

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"hyperfilelens/agent/internal/model"
)

func TestOpenInstallationSessionAcceptsAPIEnvelope(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api/v1/node/enrollment/session" {
			t.Fatalf("unexpected request %s %s", r.Method, r.URL.Path)
		}
		if r.Header.Get("X-Org-Key") != "org-a" || r.Header.Get("X-Node-Token") != "token-a" {
			t.Fatal("missing enrollment credentials")
		}
		var body map[string]string
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatal(err)
		}
		if body["role"] != "gateway" || body["installation_id"] != "hfli_host" {
			t.Fatalf("body=%v", body)
		}
		if _, present := body["session_secret"]; present {
			t.Error("normal session creation must use a server-generated secret")
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"code":0,"message":"success","data":{"installation_session":"hfls_secret","gateway_scope":"private","client_session_secret_supported":true}}`))
	}))
	defer server.Close()

	session, err := OpenInstallationSession(context.Background(), &model.AgentConfig{
		APIBaseURL: server.URL,
		OrgKey:     "org-a",
		NodeToken:  "token-a",
		Role:       model.RoleGateway,
	}, "hfli_host")
	if err != nil {
		t.Fatal(err)
	}
	if session.Secret != "hfls_secret" || session.GatewayScope != "private" || !session.ClientSessionSecretSupported {
		t.Fatalf("session=%+v", session)
	}
}

func TestOpenInstallationSessionAcceptsUnwrappedPayload(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"installation_session":"hfls_direct","gateway_scope":""}`))
	}))
	defer server.Close()

	session, err := OpenInstallationSession(context.Background(), &model.AgentConfig{
		APIBaseURL: server.URL,
		OrgKey:     "org-a",
		NodeToken:  "token-a",
		Role:       model.RoleAgent,
	}, "hfli_host")
	if err != nil {
		t.Fatal(err)
	}
	if session.Secret != "hfls_direct" || session.ClientSessionSecretSupported {
		t.Fatalf("session=%+v", session)
	}
}

func TestFetchNodeOnlineAcceptsAPIEnvelope(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/node/enrollment/node-status" {
			t.Fatalf("path=%s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"code":0,"message":"success","data":{"node_id":42,"status":"online","routable":true}}`))
	}))
	defer server.Close()

	online, status, err := fetchNodeOnline(context.Background(), &model.AgentConfig{
		APIBaseURL: server.URL,
		OrgKey:     "org-a",
		NodeToken:  "cred-a",
	}, "42")
	if err != nil {
		t.Fatal(err)
	}
	if !online || status != "online" {
		t.Fatalf("online=%v status=%q", online, status)
	}
}
