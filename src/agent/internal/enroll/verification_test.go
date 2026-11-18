package enroll

import (
	"errors"
	"strings"
	"testing"
)

func TestOnlineVerificationFailureClassifiesOnlyKnownStoppedStates(t *testing.T) {
	for _, test := range []struct {
		state string
		code  string
	}{
		{"active", "HFL-VERIFY-003"}, {"RUNNING", "HFL-VERIFY-003"},
		{"loaded", "HFL-VERIFY-003"},
		{"inactive", "HFL-VERIFY-004"}, {"STOPPED", "HFL-VERIFY-004"},
		{"failed", "HFL-VERIFY-004"}, {"not loaded", "HFL-VERIFY-005"},
		{"unknown", "HFL-VERIFY-005"}, {"unavailable", "HFL-VERIFY-005"},
		{"", "HFL-VERIFY-005"}, {"not installed", "HFL-VERIFY-005"},
		{"activating", "HFL-VERIFY-005"}, {"START_PENDING", "HFL-VERIFY-005"},
		{"STOP_PENDING", "HFL-VERIFY-005"}, {"Queued", "HFL-VERIFY-005"},
	} {
		t.Run(test.state, func(t *testing.T) {
			failure := onlineVerificationFailure(errors.New("online verification timed out"), test.state)
			if failure.CodeKey != test.code {
				t.Fatalf("code=%s, want %s", failure.CodeKey, test.code)
			}
			if test.code == "HFL-VERIFY-005" && strings.Contains(failure.Summary, "not running") {
				t.Fatal("unknown/pending service must not be reported as stopped")
			}
			if test.code != "HFL-VERIFY-003" &&
				!strings.Contains(strings.Join(failure.RecommendedActions, " "), "agent.log") {
				t.Fatal("local diagnostics must point to the Agent log")
			}
		})
	}
}
