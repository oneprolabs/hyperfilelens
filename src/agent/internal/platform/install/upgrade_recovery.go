package install

import (
	"os"
	"runtime"

	"hyperfilelens/agent/internal/platform/lifecycle"
)

// UpgradeNeedsRecovery reports states that cannot be retried through the
// incoming installer. On Unix, rollback verification is an upgrade preflight.
func UpgradeNeedsRecovery(root string) (bool, error) {
	phase, err := lifecycle.ReadUpgradePhase(root)
	if os.IsNotExist(err) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return phase == "awaiting_restart" || (runtime.GOOS == "windows" &&
		(phase == "rollback_failed" || phase == "rollback_verification_pending")), nil
}
