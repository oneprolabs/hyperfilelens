package enroll

import (
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

func TestPlanReinstallAlreadyEnrolled(t *testing.T) {
	plan, err := PlanReinstall(t.Context(), Config{OrgKey: "org-a"}, InstallState{
		Installed: true,
		OrgKey:    "org-a",
		NodeID:    "2",
		Version:   "1.0.0",
		Service:   "active",
	})
	if err != nil {
		t.Fatal(err)
	}
	if plan.Action != ActionAlreadyEnrolled {
		t.Fatalf("got %q want %q", plan.Action, ActionAlreadyEnrolled)
	}
}

func TestPlanReinstallRepair(t *testing.T) {
	plan, err := PlanReinstall(t.Context(), Config{OrgKey: "org-a"}, InstallState{
		Installed: true,
		OrgKey:    "org-a",
		NodeID:    "2",
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

func TestRoleConstraintsAgent(t *testing.T) {
	if err := roleConstraints(model.RoleAgent); err != nil {
		t.Fatalf("agent role should be allowed: %v", err)
	}
}
