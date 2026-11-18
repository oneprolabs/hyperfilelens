package enroll

import (
	"fmt"
	"path/filepath"
	"strings"
)

func onlineVerificationFailure(err error, state string) InstallFailure {
	failure := InstallFailure{
		Stage:   "Post-install verification",
		Reason:  "The Agent control-plane connection could not be confirmed: " + err.Error(),
		Code:    3,
		CodeKey: "HFL-VERIFY-003",
	}
	if isServiceHealthy(state) {
		return failure
	}
	logPath := filepath.Join(dataDirForAgent(), "logs", "agent.log")
	failure.Code = 4
	switch strings.ToLower(strings.TrimSpace(state)) {
	case "stopped", "inactive", "failed":
		failure.CodeKey = "HFL-VERIFY-004"
		failure.Summary = "Installation completed, but the Agent service is not running."
		failure.Reason = fmt.Sprintf("The Agent service is not running (service state: %s).", state)
		failure.ChangesApplied = "The Agent was installed, but its local service requires attention."
		failure.RecommendedActions = []string{
			"Review the Agent service log for the underlying error: " + logPath,
			"Correct the reported local issue, then run the same installation command again.",
		}
	default:
		// Query failures and pending recovery are not proof of a stopped daemon.
		failure.Code = 5
		failure.CodeKey = "HFL-VERIFY-005"
		failure.Summary = "Installation completed, but the Agent connection could not be confirmed."
		if strings.TrimSpace(state) == "" {
			state = "unknown"
		}
		failure.Reason = fmt.Sprintf("The Agent service state could not be confirmed as running (reported state: %s). %s", state, err)
		failure.ChangesApplied = "The Agent was installed; its current service and connection state require verification."
		failure.RecommendedActions = []string{
			"Check the Agent service status and review its log: " + logPath,
			"If the service is starting or recovering, wait briefly and check again.",
			"If verification still fails, correct the reported issue and run the same installation command again.",
		}
	}
	return failure
}
