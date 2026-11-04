package enroll

import (
	"context"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"

	"hyperfilelens/agent/internal/model"
)

func TestReleaseInstallationSessionCandidates(t *testing.T) {
	for _, active := range []string{"hfls_new", "hfls_old"} {
		t.Run(active, func(t *testing.T) {
			var requests []string
			released := false
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				secret := r.Header.Get("X-Node-Token")
				requests = append(requests, secret)
				if r.Method != http.MethodDelete || r.URL.Query().Get("installation_id") != "hfli_original" {
					t.Error("cleanup changed method or identity")
				}
				if secret != active {
					w.WriteHeader(404)
					return
				}
				released = true
				w.WriteHeader(204)
			}))
			defer server.Close()
			cfg := Config{APIBase: server.URL, OrgKey: "org", NodeRole: model.RoleAgent, InstallationID: "hfli_original", NodeToken: "broad-enrollment-token"}
			err := releaseInstallationSessionCandidates(context.Background(), cfg, []string{"hfls_old", "hfls_new", "hfls_new"})
			if err != nil || !released || !reflect.DeepEqual(requests, []string{"hfls_new", "hfls_old"}) {
				t.Fatalf("err=%v released=%v requests=%v", err, released, requests)
			}
		})
	}
}
