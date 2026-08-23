//go:build windows

package install

import (
	"strings"
	"testing"
)

func TestPsSingleQuote(t *testing.T) {
	got := psSingleQuote(`C:\ProgramData\HyperFileLens\Agent\lifecycle\upgrade\run-upgrade.ps1`)
	want := `'C:\ProgramData\HyperFileLens\Agent\lifecycle\upgrade\run-upgrade.ps1'`
	if got != want {
		t.Fatalf("psSingleQuote() = %q, want %q", got, want)
	}
	got = psSingleQuote(`C:\it's\path.ps1`)
	want = `'C:\it''s\path.ps1'`
	if got != want {
		t.Fatalf("psSingleQuote(escaped) = %q, want %q", got, want)
	}
}

func TestScheduledTaskPrincipalMatchesInstallationMode(t *testing.T) {
	userPrincipal := scheduledTaskPrincipal(true)
	if !strings.Contains(userPrincipal, "-LogonType Interactive") ||
		!strings.Contains(userPrincipal, "-RunLevel Limited") {
		t.Fatalf("current-user task principal is not interactive/limited: %s", userPrincipal)
	}
	systemPrincipal := scheduledTaskPrincipal(false)
	if !strings.Contains(systemPrincipal, "-UserId 'SYSTEM'") ||
		!strings.Contains(systemPrincipal, "-LogonType ServiceAccount") ||
		!strings.Contains(systemPrincipal, "-RunLevel Highest") {
		t.Fatalf("system task principal is not SYSTEM/service-account/highest: %s", systemPrincipal)
	}
}
