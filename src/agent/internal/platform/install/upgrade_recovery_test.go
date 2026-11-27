package install

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestUpgradePreflightReadsLegacyWindowsStateWithoutChangingPhasePolicy(t *testing.T) {
	root := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, "lifecycle"), 0700); err != nil {
		t.Fatal(err)
	}
	statePath := LifecycleUpgradeStatePath(root)
	if blocked, err := UpgradeNeedsRecovery(root); blocked || err != nil {
		t.Fatalf("missing state: blocked=%v error=%v", blocked, err)
	}
	for _, format := range []struct{ name, prefix, suffix string }{
		{"plain", "", ""},
		{"powershell-5.1", "\xef\xbb\xbf", "\r\n"},
	} {
		t.Run(format.name, func(t *testing.T) {
			for _, phase := range []string{"rollback_failed", "rollback_verification_pending", "awaiting_restart", "rolled_back"} {
				raw := format.prefix + `{"phase":"` + phase + `"}` + format.suffix
				if err := os.WriteFile(statePath, []byte(raw), 0600); err != nil {
					t.Fatal(err)
				}
				blocked, err := UpgradeNeedsRecovery(root)
				want := phase == "awaiting_restart" || (runtime.GOOS == "windows" && phase != "rolled_back")
				if err != nil || blocked != want {
					t.Fatalf("phase %s: blocked=%v, want %v, error=%v", phase, blocked, want, err)
				}
			}
		})
	}
	for _, raw := range []string{"\xef\xbb\xbf{", "\xef\xbb\xbfnull", `{}`, `{"phase":1}`} {
		if err := os.WriteFile(statePath, []byte(raw), 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := UpgradeNeedsRecovery(root); err == nil {
			t.Fatalf("accepted invalid upgrade state: %q", raw)
		}
	}
}
