package enroll

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"runtime"
	"strings"
	"time"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/tlsclient"
)

// LensSidecarConfig holds SourceLens LensNode credentials for gateway sidecar install.
type LensSidecarConfig struct {
	GatewayScope  string
	LensBaseURL   string
	LensnodeUUID  string
	LensnodeToken string
	LensnodeName  string
	WorkspaceRoot string
	Observability ObservabilityPolicy
}

// RunGatewayInstall installs the HFL agent and SourceLens LensNode sidecar for role=gateway.
func RunGatewayInstall(ctx context.Context, opts InstallOptions) error {
	if opts.Mode == InstallModeUninstall {
		LoadInstalledCommandEnv()
	}
	cfg, err := LoadConfigFromEnv()
	if err != nil {
		abortInstall("Initialization", err.Error(), 2, "HFL-INSTALL-CONFIG")
	}
	if cfg.NodeRole != model.RoleGateway {
		logFail("gateway-install requires HFL_NODE_ROLE=gateway (use the Data Gateway enrollment link)", 2)
	}
	if runtime.GOOS != "linux" {
		logFail("gateway-install is Linux-only", 2)
	}
	if opts.Mode == InstallModeUninstall {
		return RunInstall(ctx, opts)
	}
	if err := RunInstall(ctx, opts); err != nil {
		return err
	}
	commitInstallLog()
	if credential := readEnvKey(EnvFilePath(), "HFL_NODE_CREDENTIAL"); credential != "" {
		cfg.NodeToken = credential
	}
	printPhase("Installing AI engine")

	nodeID := strings.TrimSpace(ReadNodeID(EnvFilePath()))
	if nodeID == "" {
		logFail("Agent registered but node_id is missing from agent.env", 5)
	}

	logStep("Fetching AI engine configuration from the console.")
	lensCfg, err := FetchGatewayLensConfig(ctx, cfg, nodeID)
	if err != nil {
		_ = ReportGatewayInstallStatus(ctx, cfg, nodeID, "failed", err.Error())
		logFail("AI engine configuration is unavailable: "+err.Error(), 6)
	}
	// The authenticated console response is authoritative. Public Data Gateways
	// receive the deployment Sentry policy; Private Data Gateways receive disabled.
	// Observability convergence must never block enrollment or data operations.
	if changed, syncErr := SyncManagedObservabilityPolicy(lensCfg.Observability); syncErr != nil {
		logWarn("Could not persist Gateway Agent observability policy; continuing.")
	} else if changed {
		if restartErr := RestartInstalledService(ctx); restartErr != nil {
			logWarn("Could not restart Gateway Agent after observability refresh; the policy will apply on the next restart.")
		}
	}

	if err := ensureGatewayDocker(ctx, cfg); err != nil {
		_ = ReportGatewayInstallStatus(ctx, cfg, nodeID, "failed", err.Error())
		logFail("Docker setup failed: "+err.Error(), 7)
	}
	if err := InstallLensSidecar(ctx, cfg, lensCfg); err != nil {
		_ = ReportGatewayInstallStatus(ctx, cfg, nodeID, "failed", err.Error())
		logFail("AI engine install failed: "+err.Error(), 7)
	}
	_ = ReportGatewayInstallStatus(ctx, cfg, nodeID, "success", "")
	logOK("AI engine was installed successfully.")

	agentVersion := ""
	if version, versionErr := InstalledAgentVersion(ctx); versionErr == nil {
		agentVersion = version
	}
	info := summaryFromState(cfg.APIBase, nodeID, agentVersion, serviceState(ctx))
	printGatewayInstallSuccess(info, lensCfg)
	return nil
}

// FetchGatewayLensConfig retrieves LensNode credentials for an enrolled gateway.
func FetchGatewayLensConfig(ctx context.Context, cfg Config, nodeID string) (LensSidecarConfig, error) {
	base := strings.TrimRight(strings.TrimSpace(cfg.APIBase), "/")
	org := strings.TrimSpace(cfg.OrgKey)
	token := strings.TrimSpace(cfg.NodeToken)
	if base == "" || org == "" || token == "" || strings.TrimSpace(nodeID) == "" {
		return LensSidecarConfig{}, fmt.Errorf("missing API credentials or node_id")
	}

	url := fmt.Sprintf("%s/api/v1/node/enrollment/gateway-lens-config?node_id=%s", base, nodeID)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return LensSidecarConfig{}, err
	}
	req.Header.Set("X-Org-Key", org)
	req.Header.Set("X-Node-Token", token)

	client := &http.Client{Timeout: 30 * time.Second}
	if tlsclient.InsecureTLSEnabled() {
		client.Transport = tlsclient.Transport()
	}

	resp, err := client.Do(req)
	if err != nil {
		return LensSidecarConfig{}, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 8192))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return LensSidecarConfig{}, fmt.Errorf("HTTP %s: %s", resp.Status, strings.TrimSpace(string(raw)))
	}

	return parseGatewayLensConfig(raw)
}

func parseGatewayLensConfig(raw []byte) (LensSidecarConfig, error) {
	var parsed map[string]any
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return LensSidecarConfig{}, fmt.Errorf("parse response: %w", err)
	}
	data := parsed
	if nested, ok := parsed["data"].(map[string]any); ok {
		data = nested
	}
	lensRaw, ok := data["lens"].(map[string]any)
	if !ok {
		return LensSidecarConfig{}, fmt.Errorf("response missing lens block")
	}

	cfgOut := LensSidecarConfig{
		GatewayScope:  stringField(data, "gateway_scope"),
		LensBaseURL:   stringField(lensRaw, "lens_base_url"),
		LensnodeUUID:  stringField(lensRaw, "lensnode_uuid"),
		LensnodeToken: stringField(lensRaw, "lensnode_token"),
		LensnodeName:  stringField(lensRaw, "lensnode_name"),
		WorkspaceRoot: stringField(lensRaw, "workspace_root"),
	}
	if observabilityRaw, ok := data["observability"].(map[string]any); ok {
		cfgOut.Observability = ObservabilityPolicy{
			Enabled:          boolField(observabilityRaw, "enabled"),
			BackendDSN:       stringField(observabilityRaw, "backend_dsn"),
			Environment:      stringField(observabilityRaw, "environment"),
			AgentRelease:     stringField(observabilityRaw, "agent_release"),
			LensnodeRelease:  stringField(observabilityRaw, "lensnode_release"),
			TracesSampleRate: floatField(observabilityRaw, "traces_sample_rate"),
		}.Normalized()
	}
	if cfgOut.LensBaseURL == "" || cfgOut.LensnodeToken == "" || cfgOut.LensnodeUUID == "" {
		return LensSidecarConfig{}, fmt.Errorf("incomplete lens configuration from console")
	}
	if cfgOut.WorkspaceRoot == "" {
		cfgOut.WorkspaceRoot = "/workspace"
	}
	return cfgOut, nil
}

// ReportGatewayInstallStatus notifies the console when gateway-install succeeds or fails.
func ReportGatewayInstallStatus(ctx context.Context, cfg Config, nodeID, status, message string) error {
	base := strings.TrimRight(strings.TrimSpace(cfg.APIBase), "/")
	org := strings.TrimSpace(cfg.OrgKey)
	token := strings.TrimSpace(cfg.NodeToken)
	nodeID = strings.TrimSpace(nodeID)
	status = strings.TrimSpace(strings.ToLower(status))
	if base == "" || org == "" || token == "" || nodeID == "" || status == "" {
		return fmt.Errorf("missing credentials for install status report")
	}

	body, err := json.Marshal(map[string]string{
		"node_id":       nodeID,
		"status":        status,
		"error_message": strings.TrimSpace(message),
	})
	if err != nil {
		return err
	}

	url := base + "/api/v1/node/enrollment/gateway-install-status"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Org-Key", org)
	req.Header.Set("X-Node-Token", token)

	client := &http.Client{Timeout: 15 * time.Second}
	if tlsclient.InsecureTLSEnabled() {
		client.Transport = tlsclient.Transport()
	}

	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
		return fmt.Errorf("HTTP %s: %s", resp.Status, strings.TrimSpace(string(raw)))
	}
	return nil
}

func stringField(m map[string]any, key string) string {
	v, ok := m[key]
	if !ok || v == nil {
		return ""
	}
	switch s := v.(type) {
	case string:
		return strings.TrimSpace(s)
	default:
		return strings.TrimSpace(fmt.Sprint(v))
	}
}

func boolField(m map[string]any, key string) bool {
	value, ok := m[key]
	if !ok {
		return false
	}
	result, _ := value.(bool)
	return result
}

func floatField(m map[string]any, key string) float64 {
	value, ok := m[key]
	if !ok {
		return 0
	}
	switch number := value.(type) {
	case float64:
		return number
	case float32:
		return float64(number)
	default:
		return 0
	}
}

func printGatewayInstallSuccess(info SummaryInfo, lens LensSidecarConfig) {
	info.Role = gatewayDisplayName(lens.GatewayScope)
	info.LensNode = "active"
	printEnrollmentSuccess(info)
}

func gatewayDisplayName(scope string) string {
	if isPublicGatewayScope(scope) {
		return "Public Data Gateway"
	}
	return "Private Data Gateway"
}
