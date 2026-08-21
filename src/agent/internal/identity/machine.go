package identity

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"sort"
	"strings"
)

const machineFingerprintDomain = "hyperfilelens-agent-host-v1\x00"

// MachineID returns a stable, cross-platform hardware identifier for this host.
func MachineID(ctx context.Context) (string, error) {
	switch runtime.GOOS {
	case "linux":
		if value := firstFileIdentifier(
			"/etc/machine-id",
			"/var/lib/dbus/machine-id",
		); value != "" {
			return value, nil
		}
	case "darwin":
		if value := darwinPlatformUUID(ctx); value != "" {
			return value, nil
		}
	case "windows":
		if value := windowsMachineGUID(ctx); value != "" {
			return value, nil
		}
	}
	hostname, _ := os.Hostname()
	if hostname != "" {
		return "hostname:" + strings.ToLower(strings.TrimSpace(hostname)), nil
	}
	return "", fmt.Errorf("stable machine identifier is unavailable")
}

// MachineFingerprint returns a product-scoped digest suitable for control-plane
// host exclusivity checks without disclosing operating system identifiers. An
// empty value means that no sufficiently strong identifier was available; weak
// hostname fallbacks must never participate in a global uniqueness constraint.
func MachineFingerprint(ctx context.Context) (string, error) {
	identifiers := make([]string, 0, 2)
	switch runtime.GOOS {
	case "linux":
		machineID := firstFileIdentifier(
			"/etc/machine-id",
			"/var/lib/dbus/machine-id",
		)
		dmiUUID := firstFileIdentifier(
			"/sys/class/dmi/id/product_uuid",
			"/sys/devices/virtual/dmi/id/product_uuid",
		)
		if value := linuxFingerprintIdentifier(machineID, dmiUUID); value != "" {
			identifiers = append(identifiers, value)
		}
	case "darwin":
		if value := darwinPlatformUUID(ctx); usableHardwareIdentifier(value) {
			identifiers = append(identifiers, "darwin-platform-uuid:"+value)
		}
	case "windows":
		if value := windowsMachineGUID(ctx); value != "" {
			identifiers = append(identifiers, "windows-machine-guid:"+value)
		} else {
			if value := windowsProductUUID(ctx); usableHardwareIdentifier(value) {
				identifiers = append(identifiers, "windows-product-uuid:"+value)
			}
		}
	}
	return fingerprintIdentifiers(identifiers...), nil
}

func linuxFingerprintIdentifier(machineID, dmiUUID string) string {
	if machineID = strings.TrimSpace(machineID); machineID != "" {
		return "linux-machine-id:" + machineID
	}
	if dmiUUID = strings.TrimSpace(dmiUUID); usableHardwareIdentifier(dmiUUID) {
		return "linux-dmi:" + dmiUUID
	}
	return ""
}

func firstFileIdentifier(paths ...string) string {
	for _, path := range paths {
		if raw, err := os.ReadFile(path); err == nil {
			if value := strings.TrimSpace(string(raw)); value != "" {
				return value
			}
		}
	}
	return ""
}

func darwinPlatformUUID(ctx context.Context) string {
	out, err := exec.CommandContext(
		ctx,
		"ioreg",
		"-rd1",
		"-c",
		"IOPlatformExpertDevice",
	).Output()
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(out), "\n") {
		if !strings.Contains(line, "IOPlatformUUID") {
			continue
		}
		parts := strings.SplitN(line, "=", 2)
		if len(parts) == 2 {
			return strings.Trim(strings.TrimSpace(parts[1]), `"`)
		}
	}
	return ""
}

func windowsMachineGUID(ctx context.Context) string {
	return windowsPowerShellIdentifier(
		ctx,
		`(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography').MachineGuid`,
	)
}

func windowsProductUUID(ctx context.Context) string {
	return windowsPowerShellIdentifier(
		ctx,
		`(Get-CimInstance Win32_ComputerSystemProduct -ErrorAction SilentlyContinue).UUID`,
	)
}

func windowsPowerShellIdentifier(ctx context.Context, command string) string {
	out, err := exec.CommandContext(
		ctx,
		"powershell",
		"-NoProfile",
		"-Command",
		command,
	).Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

func usableHardwareIdentifier(value string) bool {
	normalized := strings.ToLower(strings.TrimSpace(value))
	normalized = strings.ReplaceAll(normalized, "-", "")
	return normalized != "" &&
		normalized != strings.Repeat("0", len(normalized)) &&
		normalized != strings.Repeat("f", len(normalized))
}

func fingerprintIdentifiers(values ...string) string {
	unique := make(map[string]struct{}, len(values))
	identifiers := make([]string, 0, len(values))
	for _, value := range values {
		value = strings.ToLower(strings.TrimSpace(value))
		if value == "" {
			continue
		}
		if _, exists := unique[value]; exists {
			continue
		}
		unique[value] = struct{}{}
		identifiers = append(identifiers, value)
	}
	if len(identifiers) == 0 {
		return ""
	}
	sort.Strings(identifiers)
	digest := sha256.Sum256([]byte(
		machineFingerprintDomain + strings.Join(identifiers, "\x00"),
	))
	return hex.EncodeToString(digest[:])
}
