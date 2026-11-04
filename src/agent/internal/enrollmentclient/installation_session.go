package enrollmentclient

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/tlsclient"
)

// InstallationSession is the server-authoritative authorization for one install.
type InstallationSession struct {
	Secret                       string
	GatewayScope                 string
	ClientSessionSecretSupported bool
}

// OpenInstallationSession exchanges an enrollment token for a resumable session.
func OpenInstallationSession(
	ctx context.Context,
	cfg *model.AgentConfig,
	installationID string,
) (InstallationSession, error) {
	return openInstallationSession(ctx, cfg, installationID, "")
}

// A client-known secret is used only for recovery on consoles advertising
// support. It lets cleanup authenticate even when the POST response is lost.
func openInstallationSession(
	ctx context.Context,
	cfg *model.AgentConfig,
	installationID, sessionSecret string,
) (InstallationSession, error) {
	base := strings.TrimRight(strings.TrimSpace(cfg.APIBaseURL), "/")
	org := strings.TrimSpace(cfg.OrgKey)
	token := strings.TrimSpace(cfg.NodeToken)
	if base == "" || org == "" || token == "" || strings.TrimSpace(installationID) == "" {
		return InstallationSession{}, fmt.Errorf("installation session credentials are incomplete")
	}
	payload := map[string]string{
		"role":            string(cfg.Role),
		"installation_id": strings.TrimSpace(installationID),
	}
	if sessionSecret != "" {
		payload["session_secret"] = sessionSecret
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return InstallationSession{}, err
	}
	req, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		base+"/api/v1/node/enrollment/session",
		bytes.NewReader(body),
	)
	if err != nil {
		return InstallationSession{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Org-Key", org)
	req.Header.Set("X-Node-Token", token)
	client := enrollmentHTTPClient(30 * time.Second)
	resp, err := client.Do(req)
	if err != nil {
		return InstallationSession{}, &RegistrationError{Operation: "installation session", Cause: err}
	}
	defer resp.Body.Close()
	raw, readErr := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return InstallationSession{}, &RegistrationError{
			Operation: "installation session", StatusCode: resp.StatusCode,
			Detail: registrationErrorDetail(raw),
		}
	}
	if readErr != nil {
		return InstallationSession{}, &RegistrationError{Operation: "installation session", Cause: readErr}
	}
	data, err := decodeAPIData(raw)
	if err != nil {
		return InstallationSession{}, fmt.Errorf("installation session response: %w", err)
	}
	secret := strings.TrimSpace(stringField(data, "installation_session"))
	if secret == "" {
		return InstallationSession{}, fmt.Errorf("installation session response is incomplete")
	}
	return InstallationSession{
		Secret:                       secret,
		GatewayScope:                 strings.TrimSpace(stringField(data, "gateway_scope")),
		ClientSessionSecretSupported: boolField(data, "client_session_secret_supported"),
	}, nil
}

// ReleaseInstallationSession releases an unfinished installation session.
func ReleaseInstallationSession(
	ctx context.Context,
	cfg *model.AgentConfig,
	installationID string,
) error {
	base := strings.TrimRight(strings.TrimSpace(cfg.APIBaseURL), "/")
	org := strings.TrimSpace(cfg.OrgKey)
	token := strings.TrimSpace(cfg.NodeToken)
	if base == "" || org == "" || token == "" || strings.TrimSpace(installationID) == "" {
		return fmt.Errorf("installation session credentials are incomplete")
	}
	body, err := json.Marshal(map[string]string{
		"role":            string(cfg.Role),
		"installation_id": strings.TrimSpace(installationID),
	})
	if err != nil {
		return err
	}
	query := url.Values{
		"role":            {string(cfg.Role)},
		"installation_id": {strings.TrimSpace(installationID)},
	}
	req, err := http.NewRequestWithContext(
		ctx,
		http.MethodDelete,
		base+"/api/v1/node/enrollment/session?"+query.Encode(),
		bytes.NewReader(body),
	)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Org-Key", org)
	req.Header.Set("X-Node-Token", token)
	resp, err := enrollmentHTTPClient(15 * time.Second).Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		return nil
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return fmt.Errorf("installation session release HTTP %s: %s", resp.Status, strings.TrimSpace(string(raw)))
	}
	return nil
}

func enrollmentHTTPClient(timeout time.Duration) *http.Client {
	client := &http.Client{Timeout: timeout}
	if tlsclient.InsecureTLSEnabled() {
		client.Transport = tlsclient.Transport()
	}
	return client
}
