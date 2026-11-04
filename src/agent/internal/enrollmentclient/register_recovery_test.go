package enrollmentclient

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/selfupdate"
)

func TestTransientRegistrationKeepsSessionWhenEnrollmentLinkExpires(t *testing.T) {
	t.Parallel()
	attempts, renewals := 0, 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/node/enrollment/session" {
			renewals++
			w.WriteHeader(401)
			io.WriteString(w, `{"error":"invalid or expired enrollment token"}`)
			return
		}
		if r.Header.Get("X-Node-Token") != "hfls_active" {
			t.Error("active session changed")
		}
		attempts++
		if attempts == 1 {
			w.WriteHeader(502)
			return
		}
		io.WriteString(w, `{"node_id":42,"node_credential":"hfln_final"}`)
	}))
	defer server.Close()
	cfg := &model.AgentConfig{APIBaseURL: server.URL, OrgKey: "org", Role: model.RoleAgent, InstallationID: "hfli_persisted", NodeToken: "hfls_active"}
	result, err := RegisterNodeHTTPWithRetry(context.Background(), cfg, selfupdate.BuildIdentity{Version: "1", Commit: "abc"}, "", RegistrationRecovery{EnrollmentSecret: "expired", ClientSessionSecretSupported: true}, nil, nil)
	if err != nil || result.NodeID != "42" || attempts != 2 || renewals != 0 {
		t.Fatalf("result=%+v err=%v attempts=%d renewals=%d", result, err, attempts, renewals)
	}
}

func TestRecoveryResponseLossKeepsKnownCleanupSecret(t *testing.T) {
	for _, mode := range []string{"first_response_lost", "both_responses_lost", "request_not_applied"} {
		t.Run(mode, func(t *testing.T) {
			t.Parallel()
			var mu sync.Mutex
			attempts, renewals := 0, 0
			activeSecret, proposed := "", ""
			known := []string{"hfls_initial"}
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				mu.Lock()
				defer mu.Unlock()
				if r.URL.Path == "/api/v1/node/nodes/heartbeat/" {
					attempts++
					switch attempts {
					case 1:
						// Registration committed, but its response was lost.
						w.WriteHeader(502)
					case 2:
						w.WriteHeader(401)
						io.WriteString(w, `{"code":401,"message":"failed","data":{"error":"invalid enrollment token"}}`)
					default:
						if r.Header.Get("X-Node-Token") != activeSecret {
							t.Error("registration used unknown session")
						}
						activeSecret = ""
						io.WriteString(w, `{"node_id":42,"node_credential":"hfln_final"}`)
					}
					return
				}
				if r.Method == http.MethodDelete {
					if r.Header.Get("X-Node-Token") == activeSecret {
						activeSecret = ""
						w.WriteHeader(204)
					} else {
						w.WriteHeader(404)
					}
					return
				}
				renewals++
				var body map[string]string
				if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
					t.Error(err)
				}
				secret := body["session_secret"]
				if len(secret) != 48 || !strings.HasPrefix(secret, "hfls_") || known[len(known)-1] != secret {
					t.Error("cleanup secret not recorded before recovery POST")
				}
				if proposed != "" && proposed != secret {
					t.Error("retry rotated the recovery secret")
				}
				proposed = secret
				if mode == "request_not_applied" {
					activeSecret = "hfls_initial"
					w.WriteHeader(401)
					io.WriteString(w, `{"error":"invalid or expired enrollment token"}`)
					return
				}
				activeSecret = secret
				if mode == "both_responses_lost" || renewals == 1 {
					conn, _, err := w.(http.Hijacker).Hijack()
					if err != nil {
						t.Error(err)
						return
					}
					conn.Close()
					return
				}
				json.NewEncoder(w).Encode(map[string]string{"installation_session": secret})
			}))
			defer server.Close()
			cfg := &model.AgentConfig{APIBaseURL: server.URL, OrgKey: "org", Role: model.RoleAgent, InstallationID: "hfli_persisted", NodeToken: "hfls_initial"}
			result, err := RegisterNodeHTTPWithRetry(context.Background(), cfg, selfupdate.BuildIdentity{Version: "1", Commit: "abc"}, "", RegistrationRecovery{EnrollmentSecret: "original", ClientSessionSecretSupported: true}, func(secret string) {
				mu.Lock()
				defer mu.Unlock()
				known = append(known, secret)
			}, nil)
			if mode == "first_response_lost" {
				if err != nil || result.NodeID != "42" || attempts != 3 || renewals != 2 {
					t.Fatalf("result=%+v err=%v attempts=%d renewals=%d", result, err, attempts, renewals)
				}
			} else {
				if err == nil || attempts != 2 {
					t.Fatalf("attempts=%d err=%v", attempts, err)
				}
				if mode == "both_responses_lost" && !IsRetryableRegistrationError(err) {
					t.Fatalf("transient retry guidance lost: %v", err)
				}
				// The installer tries all known candidates, including the old
				// secret if the recovery request never reached the console.
				for i := len(known) - 1; i >= 0; i-- {
					candidate := *cfg
					candidate.NodeToken = known[i]
					if err := ReleaseInstallationSession(context.Background(), &candidate, cfg.InstallationID); err != nil {
						t.Fatal(err)
					}
				}
			}
			mu.Lock()
			defer mu.Unlock()
			if activeSecret != "" || cfg.InstallationID != "hfli_persisted" {
				t.Fatal("session leaked or identity changed")
			}
		})
	}
}

func TestOldConsoleDoesNotAttemptUnsafeRecovery(t *testing.T) {
	t.Parallel()
	attempts, renewals := 0, 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/node/enrollment/session" {
			renewals++
			t.Error("old console must not rotate to an unknown recovery secret")
			return
		}
		attempts++
		if attempts == 1 {
			w.WriteHeader(502)
			return
		}
		w.WriteHeader(401)
		io.WriteString(w, `{"error":"invalid enrollment token"}`)
	}))
	defer server.Close()
	cfg := &model.AgentConfig{APIBaseURL: server.URL, OrgKey: "org", Role: model.RoleAgent, InstallationID: "hfli_persisted", NodeToken: "hfls_initial"}
	_, err := RegisterNodeHTTPWithRetry(context.Background(), cfg, selfupdate.BuildIdentity{Version: "1", Commit: "abc"}, "", RegistrationRecovery{EnrollmentSecret: "original"}, nil, nil)
	if attempts != 2 || renewals != 0 || !IsRetryableRegistrationError(err) || cfg.NodeToken != "hfls_initial" {
		t.Fatalf("attempts=%d renewals=%d err=%v", attempts, renewals, err)
	}
}

func TestExhaustedAmbiguousRegistrationKeepsTransientGuidance(t *testing.T) {
	t.Parallel()
	attempts := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/node/nodes/heartbeat/" {
			t.Error("exhausted budget must not renew a session")
			w.WriteHeader(500)
			return
		}
		attempts++
		if attempts < 3 {
			w.WriteHeader(502)
			return
		}
		w.WriteHeader(401)
		io.WriteString(w, `{"error":"invalid enrollment token"}`)
	}))
	defer server.Close()
	cfg := &model.AgentConfig{APIBaseURL: server.URL, OrgKey: "org", Role: model.RoleAgent, InstallationID: "hfli_persisted", NodeToken: "hfls_initial"}
	_, err := RegisterNodeHTTPWithRetry(context.Background(), cfg, selfupdate.BuildIdentity{Version: "1", Commit: "abc"}, "", RegistrationRecovery{EnrollmentSecret: "original", ClientSessionSecretSupported: true}, nil, nil)
	if attempts != 3 || !IsRetryableRegistrationError(err) {
		t.Fatalf("attempts=%d err=%v", attempts, err)
	}
}

func TestRegistrationErrorDetailAcceptsConsoleEnvelope(t *testing.T) {
	for _, detail := range []string{
		"installation mode does not match enrollment token",
		"installation mode authorization changed",
		"installation session does not match this installation identity",
		"invalid enrollment token",
		"invalid or expired enrollment token",
	} {
		raw := `{"code":409,"message":"failed","data":{"error":"` + detail + `"}}`
		if got := registrationErrorDetail([]byte(raw)); got != detail {
			t.Errorf("detail=%q want=%q", got, detail)
		}
	}
	for _, raw := range []string{
		`{"code":"SUBSCRIPTION.QUOTA_EXCEEDED","detail":"private quota details"}`,
		`{"code":409,"message":"failed","data":{"code":"SUBSCRIPTION.QUOTA_EXCEEDED","detail":"private quota details"}}`,
	} {
		if got := registrationErrorDetail([]byte(raw)); got != "Organization quota is full." {
			t.Errorf("quota reason lost: %q", got)
		}
	}
	if got := registrationErrorDetail([]byte(`{"code":502,"data":{"error":"secret=private"}}`)); got != "" {
		t.Fatalf("untrusted nested detail: %q", got)
	}
}
