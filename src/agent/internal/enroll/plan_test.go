package enroll

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"hyperfilelens/agent/internal/model"
)

func TestVersionGreater(t *testing.T) {
	cases := []struct {
		a, b   string
		wantGT bool
	}{
		{"1.1.0", "1.0.0", true},
		{"1.0.0", "1.1.0", false},
		{"1.0.0", "1.0.0", false},
		{"2.0", "1.9.9", true},
		{"unknown", "1.0.0", false},
		{"", "1.0.0", false},
	}
	for _, tc := range cases {
		got := versionGreater(tc.a, tc.b)
		if got != tc.wantGT {
			t.Fatalf("versionGreater(%q,%q)=%v want %v", tc.a, tc.b, got, tc.wantGT)
		}
	}
}

func TestIsServiceHealthy(t *testing.T) {
	if !isServiceHealthy("active") {
		t.Fatal("expected active")
	}
	if !isServiceHealthy("RUNNING") {
		t.Fatal("expected RUNNING")
	}
	if isServiceHealthy("inactive") {
		t.Fatal("expected inactive")
	}
}

func TestShouldRecoverServiceAfterConfigRestore(t *testing.T) {
	tests := []struct {
		name        string
		wasRunning  bool
		current     string
		wantRecover bool
	}{
		{name: "running service remains active", wasRunning: true, current: "active", wantRecover: false},
		{name: "running service became inactive", wasRunning: true, current: "inactive", wantRecover: true},
		{name: "inactive service stays inactive", wasRunning: false, current: "inactive", wantRecover: false},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got := shouldRecoverServiceAfterConfigRestore(test.wasRunning, test.current)
			if got != test.wantRecover {
				t.Fatalf("shouldRecoverServiceAfterConfigRestore(%v, %q) = %v, want %v", test.wasRunning, test.current, got, test.wantRecover)
			}
		})
	}
}

func TestPlanReinstallCrossOrg(t *testing.T) {
	plan, err := PlanReinstall(t.Context(), Config{OrgKey: "org-b"}, InstallState{
		Installed: true,
		OrgKey:    "org-a",
		NodeID:    "1",
		Service:   "active",
	})
	if err != nil {
		t.Fatal(err)
	}
	if plan.Action != ActionCrossOrg {
		t.Fatalf("got %q want %q", plan.Action, ActionCrossOrg)
	}
}

func TestPlanReinstallRejectsRoleReplacement(t *testing.T) {
	_, err := PlanReinstall(t.Context(), Config{
		OrgKey:   "org-a",
		NodeRole: model.RoleGateway,
	}, InstallState{
		Installed: true,
		OrgKey:    "org-a",
		Role:      string(model.RoleAgent),
		NodeID:    "1",
		Service:   "active",
	})
	if err == nil {
		t.Fatal("expected cross-role replacement to be rejected")
	}
}

func TestPlanReinstallReconcilesHealthyExistingInstallation(t *testing.T) {
	plan, err := PlanReinstall(t.Context(), Config{OrgKey: "org-a", APIBase: "https://console.example"}, InstallState{
		Installed: true,
		OrgKey:    "org-a",
		APIBase:   "https://console.example/",
		NodeID:    "2",
		Version:   "1.0.0",
		Service:   "active",
	})
	if err != nil {
		t.Fatal(err)
	}
	if plan.Action != ActionReconcile {
		t.Fatalf("got %q want %q", plan.Action, ActionReconcile)
	}
}

func TestPlanInstallRejectsControlPlaneChange(t *testing.T) {
	plan, err := PlanInstall(t.Context(), Config{
		OrgKey:   "org-a",
		APIBase:  "https://new-console.example",
		NodeRole: model.RoleAgent,
	}, InstallState{
		Installed: true,
		OrgKey:    "org-a",
		APIBase:   "https://old-console.example",
		NodeID:    "2",
		Service:   "active",
	}, InstallModeAuto)
	if err == nil || plan.Action != "" {
		t.Fatalf("control plane change plan = %#v, err=%v; want a rejected in-place switch", plan, err)
	}
}

func TestPlanInstallNormalizesEquivalentControlPlaneAddresses(t *testing.T) {
	plan, err := PlanInstall(t.Context(), Config{
		OrgKey:   "org-a",
		APIBase:  "https://CONSOLE.example:443/",
		NodeRole: model.RoleAgent,
	}, InstallState{
		Installed: true,
		OrgKey:    "org-a",
		APIBase:   "https://console.example",
		NodeID:    "2",
		Version:   "1.0.0",
		Service:   "active",
	}, InstallModeAuto)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Action != ActionReconcile {
		t.Fatalf("got %q want %q", plan.Action, ActionReconcile)
	}
}

func TestPlanInstallRejectsWebSocketEndpointChange(t *testing.T) {
	_, err := PlanInstall(t.Context(), Config{
		OrgKey:   "org-a",
		APIBase:  "https://console.example",
		WSSURL:   "wss://console.example/ws/node/agent/",
		NodeRole: model.RoleAgent,
	}, InstallState{
		Installed: true,
		OrgKey:    "org-a",
		APIBase:   "https://console.example",
		WSSURL:    "wss://old-console.example/ws/node/agent/",
		NodeID:    "2",
		Service:   "active",
	}, InstallModeAuto)
	if err == nil {
		t.Fatal("expected WebSocket endpoint change to be rejected")
	}
}

func TestPlanInstallRejectsMissingControlPlaneIdentity(t *testing.T) {
	_, err := PlanInstall(t.Context(), Config{
		OrgKey:   "org-a",
		APIBase:  "https://console.example",
		NodeRole: model.RoleAgent,
	}, InstallState{
		Installed: true,
		OrgKey:    "org-a",
		NodeID:    "2",
		Service:   "active",
	}, InstallModeAuto)
	if err == nil {
		t.Fatal("expected a registered installation with no stored control plane to be rejected")
	}
}

func TestPlanInstallRejectsInstallationModeChange(t *testing.T) {
	_, err := PlanInstall(t.Context(), Config{
		OrgKey:           "org-a",
		APIBase:          "https://console.example",
		NodeRole:         model.RoleAgent,
		InstallationMode: model.InstallationModeSystem,
	}, InstallState{
		Installed:        true,
		OrgKey:           "org-a",
		Role:             string(model.RoleAgent),
		InstallationMode: string(model.InstallationModeUser),
		NodeID:           "2",
		Service:          "active",
	}, InstallModeAuto)
	if err == nil {
		t.Fatal("expected installation mode change to be rejected")
	}
}

func TestPlanInstallRejectsGatewayScopeChange(t *testing.T) {
	_, err := PlanInstall(t.Context(), Config{
		OrgKey:       "org-a",
		APIBase:      "https://console.example",
		NodeRole:     model.RoleGateway,
		GatewayScope: "private",
	}, InstallState{
		Installed:    true,
		OrgKey:       "org-a",
		Role:         string(model.RoleGateway),
		GatewayScope: "platform",
		NodeID:       "2",
		Service:      "active",
	}, InstallModeAuto)
	if err == nil {
		t.Fatal("expected Gateway scope change to be rejected")
	}
}

func TestPlanInstallAllowsGatewayScopeToBeResolvedBySession(t *testing.T) {
	plan, err := PlanInstall(t.Context(), Config{
		OrgKey:   "org-a",
		APIBase:  "https://console.example",
		NodeRole: model.RoleGateway,
	}, InstallState{
		Installed:    true,
		OrgKey:       "org-a",
		Role:         string(model.RoleGateway),
		GatewayScope: "private",
		APIBase:      "https://console.example",
		NodeID:       "2",
		Service:      "active",
	}, InstallModeAuto)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Action != ActionReconcile {
		t.Fatalf("got %q want %q", plan.Action, ActionReconcile)
	}
}

func TestPlanInstallUpgradesBeforeReconcilingRegistration(t *testing.T) {
	server := releaseServer(t, "2.0.0")
	defer server.Close()

	plan, err := PlanInstall(t.Context(), Config{
		OrgKey:    "org-a",
		NodeToken: "enrollment-token",
		APIBase:   server.URL,
		NodeRole:  model.RoleAgent,
	}, InstallState{
		Installed: true,
		OrgKey:    "org-a",
		APIBase:   server.URL,
		NodeID:    "2",
		Version:   "1.0.0",
		Service:   "active",
	}, InstallModeAuto)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Action != ActionUpgrade {
		t.Fatalf("got %q want %q", plan.Action, ActionUpgrade)
	}
}

func TestPlanInstallReconcilesWithoutDowngrading(t *testing.T) {
	server := releaseServer(t, "1.0.0")
	defer server.Close()

	plan, err := PlanInstall(t.Context(), Config{
		OrgKey:    "org-a",
		NodeToken: "enrollment-token",
		APIBase:   server.URL,
		NodeRole:  model.RoleAgent,
	}, InstallState{
		Installed: true,
		OrgKey:    "org-a",
		APIBase:   server.URL,
		NodeID:    "2",
		Version:   "2.0.0",
		Service:   "active",
	}, InstallModeAuto)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Action != ActionReconcile {
		t.Fatalf("got %q want %q", plan.Action, ActionReconcile)
	}
}

func TestPlanExplicitUpgradeDoesNotDowngrade(t *testing.T) {
	server := releaseServer(t, "1.0.0")
	defer server.Close()

	plan, err := PlanInstall(t.Context(), Config{
		OrgKey:    "org-a",
		NodeToken: "enrollment-token",
		APIBase:   server.URL,
		NodeRole:  model.RoleAgent,
	}, InstallState{
		Installed: true,
		OrgKey:    "org-a",
		APIBase:   server.URL,
		NodeID:    "2",
		Version:   "2.0.0",
		Service:   "active",
	}, InstallModeUpgrade)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Action != ActionReconcile {
		t.Fatalf("got %q want %q", plan.Action, ActionReconcile)
	}
}

func TestPlanReinstallRepair(t *testing.T) {
	plan, err := PlanReinstall(t.Context(), Config{
		OrgKey:  "org-a",
		APIBase: "https://console.example",
	}, InstallState{
		Installed: true,
		OrgKey:    "org-a",
		NodeID:    "2",
		APIBase:   "https://console.example",
		Version:   "1.0.0",
		Service:   "inactive",
	})
	if err != nil {
		t.Fatal(err)
	}
	if plan.Action != ActionRepair {
		t.Fatalf("got %q want %q", plan.Action, ActionRepair)
	}
	if !plan.NeedsConfirm {
		t.Fatal("repair should require confirmation")
	}
}

func releaseServer(t *testing.T, version string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":{"download_url":"https://downloads.example/agent.tar.gz","version":"` + version + `"}}`))
	}))
}

func TestPlanReinstallRebind(t *testing.T) {
	plan, err := PlanReinstall(t.Context(), Config{OrgKey: "org-a"}, InstallState{
		Installed: true,
		OrgKey:    "org-a",
		Service:   "inactive",
	})
	if err != nil {
		t.Fatal(err)
	}
	if plan.Action != ActionRebind {
		t.Fatalf("got %q want %q", plan.Action, ActionRebind)
	}
}

func TestRegisterConfirmationMessage(t *testing.T) {
	tests := []struct {
		name  string
		state InstallState
		want  string
	}{
		{
			name:  "unregistered",
			state: InstallState{Installed: true, Service: "active"},
			want:  "The agent is installed but not registered with the console. Bind this host now?",
		},
		{
			name:  "inactive",
			state: InstallState{Installed: true, NodeID: "2", Service: "inactive"},
			want:  "Node 2 is enrolled, but the service is inactive. Restart the agent service and reconnect to the console?",
		},
		{
			name:  "healthy",
			state: InstallState{Installed: true, NodeID: "2", Service: "active"},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := registerConfirmationMessage(test.state); got != test.want {
				t.Fatalf("got %q want %q", got, test.want)
			}
		})
	}
}

func TestRoleConstraintsAgent(t *testing.T) {
	if err := roleConstraints(model.RoleAgent); err != nil {
		t.Fatalf("agent role should be allowed: %v", err)
	}
}
