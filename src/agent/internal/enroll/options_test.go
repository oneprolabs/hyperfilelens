package enroll

import "testing"

func TestParseInstallOptionsExplicitModes(t *testing.T) {
	tests := []struct {
		flag string
		want InstallMode
	}{
		{"--upgrade", InstallModeUpgrade},
		{"--repair", InstallModeRepair},
		{"--reinstall", InstallModeReinstall},
		{"--uninstall", InstallModeUninstall},
	}
	for _, test := range tests {
		t.Run(test.flag, func(t *testing.T) {
			opts := ParseInstallOptions([]string{test.flag, "--yes"})
			if opts.Mode != test.want || !opts.AutoYes || opts.Invalid != "" {
				t.Fatalf("options=%+v", opts)
			}
		})
	}
}

func TestParseInstallOptionsRejectsConflictingModes(t *testing.T) {
	opts := ParseInstallOptions([]string{"--upgrade", "--repair"})
	if opts.Invalid == "" {
		t.Fatalf("expected conflicting lifecycle modes to be rejected: %+v", opts)
	}
}

func TestParseInstallOptionsRequiresUninstallForPurge(t *testing.T) {
	opts := ParseInstallOptions([]string{"--purge-all"})
	if opts.Invalid == "" {
		t.Fatalf("expected standalone --purge-all to be rejected: %+v", opts)
	}
}

func TestParseInstallOptionsAcceptsPurgeCompatibilityForUninstall(t *testing.T) {
	opts := ParseInstallOptions([]string{"--uninstall", "--purge-all"})
	if opts.Invalid != "" || opts.KeepData || !opts.PurgeAll {
		t.Fatalf("expected purge-all compatibility uninstall options: %+v", opts)
	}
}

func TestParseInstallOptionsKeepsDataOnlyForUninstall(t *testing.T) {
	opts := ParseInstallOptions([]string{"--uninstall", "--keep-data"})
	if opts.Invalid != "" || !opts.KeepData || opts.PurgeAll {
		t.Fatalf("expected keep-data uninstall options: %+v", opts)
	}

	opts = ParseInstallOptions([]string{"--keep-data"})
	if opts.Invalid == "" {
		t.Fatalf("expected standalone --keep-data to be rejected: %+v", opts)
	}
}

func TestParseInstallOptionsRejectsConflictingUninstallPolicies(t *testing.T) {
	opts := ParseInstallOptions([]string{"--uninstall", "--keep-data", "--purge-all"})
	if opts.Invalid == "" {
		t.Fatalf("expected conflicting uninstall policies to be rejected: %+v", opts)
	}
}

func TestParseInstallOptionsRejectsInvalidOutputMode(t *testing.T) {
	for _, args := range [][]string{{"--output"}, {"--output", "xml"}, {"--output=xml"}} {
		opts := ParseInstallOptions(args)
		if opts.Invalid == "" {
			t.Fatalf("expected invalid output option to be rejected: %v", args)
		}
	}
}

func TestParseInstallOptionsRejectsUnknownFlag(t *testing.T) {
	opts := ParseInstallOptions([]string{"--unknown"})
	if opts.Invalid == "" {
		t.Fatalf("expected unknown option to be rejected: %+v", opts)
	}
}
