//go:build !windows

package enroll

import "testing"

func TestRunningExecutableMatchesRejectsDeletedProcess(t *testing.T) {
	if runningExecutableMatches("/opt/hyperfilelens-agent/bin/hfl-agent (deleted)", "/opt/hyperfilelens-agent/bin/hfl-agent") {
		t.Fatal("a deleted executable must not be accepted as the installed Agent")
	}
}

func TestRunningExecutableMatchesAcceptsExactPath(t *testing.T) {
	if !runningExecutableMatches("/opt/hyperfilelens-agent/bin/hfl-agent", "/opt/hyperfilelens-agent/bin/hfl-agent") {
		t.Fatal("the exact installed executable should be accepted")
	}
}

func TestEnrollmentServiceAction(t *testing.T) {
	cases := []struct {
		name, want                     string
		mayHaveProcess, pendingUpgrade bool
	}{
		{name: "inactive", want: "start"},
		{name: "active", mayHaveProcess: true, want: "restart"},
		{name: "pending upgrade", pendingUpgrade: true, want: "start"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := enrollmentServiceAction(tc.mayHaveProcess, tc.pendingUpgrade); got != tc.want {
				t.Fatalf("enrollmentServiceAction() = %q, want %q", got, tc.want)
			}
		})
	}
}
