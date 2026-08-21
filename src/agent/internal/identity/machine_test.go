package identity

import (
	"context"
	"testing"
)

func TestMachineFingerprintIsStableProductScopedDigest(t *testing.T) {
	first, err := MachineFingerprint(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	second, err := MachineFingerprint(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if first != second || len(first) != 64 {
		t.Fatalf("machine fingerprint is not a stable SHA-256 digest: %q %q", first, second)
	}
	machineID, err := MachineID(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if first == machineID {
		t.Fatal("machine fingerprint must not disclose the operating system identifier")
	}
}

func TestFingerprintIdentifiersRejectsWeakEmptyInput(t *testing.T) {
	if got := fingerprintIdentifiers(); got != "" {
		t.Fatalf("empty identifiers produced fingerprint %q", got)
	}
	if got := fingerprintIdentifiers("", "   "); got != "" {
		t.Fatalf("blank identifiers produced fingerprint %q", got)
	}
}

func TestFingerprintIdentifiersIsStableAndOrderIndependent(t *testing.T) {
	first := fingerprintIdentifiers("linux-machine-id:ABC", "linux-dmi:DEF")
	second := fingerprintIdentifiers("linux-dmi:def", "linux-machine-id:abc")
	if first == "" || first != second || len(first) != 64 {
		t.Fatalf("fingerprint is not stable: %q %q", first, second)
	}
}

func TestUsableHardwareIdentifierRejectsFirmwarePlaceholders(t *testing.T) {
	for _, value := range []string{
		"",
		"00000000-0000-0000-0000-000000000000",
		"FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF",
	} {
		if usableHardwareIdentifier(value) {
			t.Fatalf("placeholder hardware identifier accepted: %q", value)
		}
	}
	if !usableHardwareIdentifier("f81d4fae-7dec-11d0-a765-00a0c91e6bf6") {
		t.Fatal("valid hardware identifier rejected")
	}
}

func TestLinuxFingerprintIdentifierPrefersUnprivilegedMachineID(t *testing.T) {
	withDMI := linuxFingerprintIdentifier("machine-123", "dmi-456")
	withoutDMI := linuxFingerprintIdentifier("machine-123", "")
	if withDMI != "linux-machine-id:machine-123" || withDMI != withoutDMI {
		t.Fatalf("Linux identifier changed with DMI visibility: %q %q", withDMI, withoutDMI)
	}
	if fallback := linuxFingerprintIdentifier("", "f81d4fae-7dec-11d0-a765-00a0c91e6bf6"); fallback != "linux-dmi:f81d4fae-7dec-11d0-a765-00a0c91e6bf6" {
		t.Fatalf("DMI fallback = %q", fallback)
	}
}
