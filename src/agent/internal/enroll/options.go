package enroll

import (
	"os"
	"strings"
)

// InstallMode selects an explicit lifecycle path or safe automatic planning.
type InstallMode string

const (
	InstallModeAuto      InstallMode = "auto"
	InstallModeUpgrade   InstallMode = "upgrade"
	InstallModeRepair    InstallMode = "repair"
	InstallModeReinstall InstallMode = "reinstall"
	InstallModeUninstall InstallMode = "uninstall"
)

// InstallOptions controls non-interactive enrollment behavior.
type InstallOptions struct {
	AutoYes  bool
	Mode     InstallMode
	KeepData bool
	PurgeAll bool
	Invalid  string
}

// ParseInstallOptions reads flags after `hfl-enroll install`.
func ParseInstallOptions(args []string) InstallOptions {
	opts := InstallOptions{Mode: InstallModeAuto}
	setMode := func(mode InstallMode) {
		if opts.Mode != InstallModeAuto && opts.Mode != mode {
			opts.Invalid = "choose only one of --upgrade, --repair, --reinstall, or --uninstall"
			return
		}
		opts.Mode = mode
	}
	for index := 0; index < len(args); index++ {
		a := args[index]
		switch a {
		case "--yes", "-y":
			opts.AutoYes = true
		case "--upgrade":
			setMode(InstallModeUpgrade)
		case "--repair":
			setMode(InstallModeRepair)
		case "--reinstall":
			setMode(InstallModeReinstall)
		case "--uninstall":
			setMode(InstallModeUninstall)
		case "--keep-data":
			opts.KeepData = true
		case "--purge-all":
			opts.PurgeAll = true
		case "--no-color":
			_ = os.Setenv("NO_COLOR", "1")
		case "--no-banner":
			_ = os.Setenv("HFL_NO_BANNER", "1")
		case "--verbose":
			_ = os.Setenv("HFL_VERBOSE", "1")
		case "--output":
			if index+1 >= len(args) {
				opts.Invalid = "--output requires auto, rich, plain, or json"
				continue
			}
			index++
			if !setOutputMode(args[index]) {
				opts.Invalid = "--output requires auto, rich, plain, or json"
			}
		default:
			if strings.HasPrefix(a, "--output=") {
				if !setOutputMode(strings.TrimPrefix(a, "--output=")) {
					opts.Invalid = "--output requires auto, rich, plain, or json"
				}
			} else {
				opts.Invalid = "unknown install option: " + a
			}
		}
	}
	if opts.PurgeAll && opts.Mode != InstallModeUninstall {
		opts.Invalid = "--purge-all requires --uninstall"
	}
	if opts.KeepData && opts.Mode != InstallModeUninstall {
		opts.Invalid = "--keep-data requires --uninstall"
	}
	if opts.KeepData && opts.PurgeAll {
		opts.Invalid = "--keep-data and --purge-all are mutually exclusive"
	}
	return opts
}

func setOutputMode(value string) bool {
	value = strings.ToLower(strings.TrimSpace(value))
	switch value {
	case "auto", "rich", "plain", "json":
		_ = os.Setenv("HFL_OUTPUT", value)
		return true
	}
	return false
}
