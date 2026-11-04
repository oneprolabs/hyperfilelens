package enrollmentclient

import (
	"context"
	"crypto/x509"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/selfupdate"
)

func TestRegistrationRetryPreservesInstallationIdentity(t *testing.T) {
	for _, test := range []struct {
		name       string
		statuses   []int
		disconnect bool
	}{
		{"success_without_retry", []int{200}, false},
		{"502_then_success", []int{502, 200}, false},
		{"429_then_success", []int{429, 200}, false},
		{"408_then_success", []int{408, 200}, false},
		{"503_twice_then_success", []int{503, 503, 200}, false},
		{"lost_committed_response", []int{502, 200}, true},
		{"exhausted", []int{504, 504, 504}, false},
	} {
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			attempts, sessions, notices, released := 0, 0, 0, false
			currentSecret := "hfls_initial"
			nodes := map[string]string{}
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var body map[string]any
				if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
					t.Error(err)
					return
				}
				if body["installation_id"] != "hfli_persisted" || body["role"] != "agent" {
					t.Errorf("changed identity: %v", body)
				}
				switch r.URL.Path {
				case "/api/v1/node/enrollment/session":
					if r.Method == http.MethodDelete {
						if r.Header.Get("X-Node-Token") != currentSecret {
							t.Error("cleanup used stale secret")
						}
						released = true
						w.WriteHeader(http.StatusNoContent)
						return
					}
					if r.Header.Get("X-Node-Token") != "original-enrollment" {
						t.Error("recovery did not use original authorization")
					}
					sessions++
					currentSecret = "hfls_recovered_" + strconv.Itoa(sessions)
					w.WriteHeader(http.StatusCreated)
					json.NewEncoder(w).Encode(map[string]string{"installation_session": currentSecret})
				case "/api/v1/node/nodes/heartbeat/":
					if r.Header.Get("X-Node-Token") != currentSecret {
						t.Error("stale session secret")
					}
					if body["existing_node_credential"] != "hfln_existing" {
						t.Error("credential reuse proof was lost")
					}
					if _, ok := body["node_id"]; ok {
						t.Error("installation registration must omit node_id")
					}
					attempts++
					if test.disconnect && attempts == 1 {
						nodes["hfli_persisted"] = "42" // Commit succeeds, response is lost.
						conn, _, err := w.(http.Hijacker).Hijack()
						if err != nil {
							t.Error(err)
							return
						}
						conn.Close()
						return
					}
					status := test.statuses[attempts-1]
					if status != 200 {
						w.WriteHeader(status)
						io.WriteString(w, "<html>proxy error with secret</html>")
						return
					}
					nodes["hfli_persisted"] = "42"
					io.WriteString(w, `{"node_id":42,"node_credential":"hfln_final"}`)
				default:
					t.Errorf("unexpected endpoint %s", r.URL.Path)
				}
			}))
			defer server.Close()
			cfg := &model.AgentConfig{APIBaseURL: server.URL, OrgKey: "org", Role: model.RoleAgent, InstallationID: "hfli_persisted", NodeToken: currentSecret}
			cleanupSecret := cfg.NodeToken
			result, err := RegisterNodeHTTPWithRetry(context.Background(), cfg, selfupdate.BuildIdentity{Version: "1.2.3", Commit: "abc"}, "hfln_existing", "original-enrollment", func(s string) { cleanupSecret = s }, func(attempt, max int, err error) {
				notices++
				if max != 3 {
					t.Error("unbounded attempts")
				}
			})
			if attempts != len(test.statuses) || sessions != attempts-1 || notices != attempts-1 {
				t.Errorf("attempts=%d sessions=%d notices=%d", attempts, sessions, notices)
			}
			if cleanupSecret != currentSecret || cfg.InstallationID != "hfli_persisted" {
				t.Error("cleanup/identity state was not preserved")
			}
			if test.name == "exhausted" {
				cleanupCfg := *cfg
				cleanupCfg.NodeToken = cleanupSecret
				if releaseErr := ReleaseInstallationSession(context.Background(), &cleanupCfg, cfg.InstallationID); releaseErr != nil {
					t.Fatal(releaseErr)
				}
				if !released {
					t.Fatal("latest session was not released")
				}
				if !IsRetryableRegistrationError(err) || len(nodes) != 0 {
					t.Fatalf("err=%v nodes=%v", err, nodes)
				}
				return
			}
			if err != nil || result.NodeID != "42" || result.NodeCredential != "hfln_final" || len(nodes) != 1 {
				t.Fatalf("result=%+v err=%v nodes=%v", result, err, nodes)
			}
		})
	}
}

func TestRegistrationPermanentErrorsAreNotRetried(t *testing.T) {
	for _, status := range []int{400, 401, 403, 409, 422, 500} {
		t.Run(strconv.Itoa(status), func(t *testing.T) {
			attempts := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				attempts++
				w.WriteHeader(status)
				io.WriteString(w, `{"error":"private-server-body"}`)
			}))
			defer server.Close()
			cfg := &model.AgentConfig{APIBaseURL: server.URL, OrgKey: "org", Role: model.RoleAgent, InstallationID: "hfli_persisted", NodeToken: "hfls_initial"}
			_, err := RegisterNodeHTTPWithRetry(context.Background(), cfg, selfupdate.BuildIdentity{Version: "1", Commit: "abc"}, "", "original", nil, nil)
			if err == nil || attempts != 1 || IsRetryableRegistrationError(err) {
				t.Fatalf("attempts=%d err=%v", attempts, err)
			}
			if strings.Contains(err.Error(), "private-server-body") {
				t.Fatal("response body exposed")
			}
		})
	}
}

func TestRegistrationCancellationStopsBeforeRecovery(t *testing.T) {
	attempts := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { attempts++; w.WriteHeader(502) }))
	defer server.Close()
	cfg := &model.AgentConfig{APIBaseURL: server.URL, OrgKey: "org", Role: model.RoleAgent, InstallationID: "hfli_persisted", NodeToken: "hfls_initial"}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	_, err := RegisterNodeHTTPWithRetry(ctx, cfg, selfupdate.BuildIdentity{Version: "1", Commit: "abc"}, "", "original", nil, func(int, int, error) { cancel() })
	if !errors.Is(err, context.Canceled) || attempts != 1 {
		t.Fatalf("attempts=%d err=%v", attempts, err)
	}
}

func TestRegistrationErrorClassificationAndPrivacy(t *testing.T) {
	for _, err := range []error{syscall.ECONNRESET, syscall.ECONNREFUSED, io.EOF, io.ErrUnexpectedEOF, context.DeadlineExceeded} {
		wrapped := &RegistrationError{Cause: &url.Error{Op: "POST", URL: "https://example/?token=secret", Err: err}}
		if !IsRetryableRegistrationError(wrapped) || strings.Contains(wrapped.Error(), "secret") {
			t.Errorf("unsafe classification: %v", wrapped)
		}
	}
	for _, err := range []error{context.Canceled, x509.UnknownAuthorityError{}, errors.New("invalid configuration")} {
		if IsRetryableRegistrationError(&RegistrationError{Cause: err}) {
			t.Errorf("permanent error retried: %v", err)
		}
	}
}

func TestRegistrationRetryAfterIsBounded(t *testing.T) {
	for value, want := range map[string]time.Duration{"": 0, "-1": 0, "0": 0, "2": 2 * time.Second, "999999999": 5 * time.Second, "garbage": 0} {
		if got := registrationRetryAfter(value); got != want {
			t.Errorf("%q: %v != %v", value, got, want)
		}
	}
	if got := registrationRetryAfter(time.Now().Add(time.Hour).UTC().Format(http.TimeFormat)); got != 5*time.Second {
		t.Errorf("HTTP-date delay=%v", got)
	}
}

func TestRegistrationRecoveryStopsOnPermanentError(t *testing.T) {
	for _, status := range []int{401, 409} {
		t.Run(strconv.Itoa(status), func(t *testing.T) {
			attempts, sessions := 0, 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/api/v1/node/enrollment/session" {
					sessions++
					io.WriteString(w, `{"installation_session":"hfls_new"}`)
					return
				}
				attempts++
				if attempts == 1 {
					w.WriteHeader(502)
					return
				}
				w.WriteHeader(status)
			}))
			defer server.Close()
			cfg := &model.AgentConfig{APIBaseURL: server.URL, OrgKey: "org", Role: model.RoleAgent, InstallationID: "hfli_existing", NodeToken: "hfls_initial"}
			_, err := RegisterNodeHTTPWithRetry(context.Background(), cfg, selfupdate.BuildIdentity{Version: "1", Commit: "abc"}, "", "original", nil, nil)
			if err == nil || attempts != 2 || sessions != 1 || IsRetryableRegistrationError(err) {
				t.Fatalf("attempts=%d sessions=%d err=%v", attempts, sessions, err)
			}
		})
	}
}

func TestRegistrationErrorDetailNeverCopiesArbitraryResponse(t *testing.T) {
	for _, raw := range []string{`<html>token=secret</html>`, `{"error":"echoed hfls_secret"}`, `{"detail":"private file"}`} {
		if got := registrationErrorDetail([]byte(raw)); got != "" {
			t.Fatalf("untrusted detail: %s", got)
		}
	}
	want := "installation mode does not match enrollment token"
	if got := registrationErrorDetail([]byte(`{"error":"` + want + `"}`)); got != want {
		t.Fatalf("protocol reason lost: %s", got)
	}
}
