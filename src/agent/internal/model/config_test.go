package model

import "testing"

func TestUserContinuousInstallationModeContract(t *testing.T) {
	mode, err := ParseInstallationMode("user_continuous")
	if err != nil {
		t.Fatal(err)
	}
	if mode != InstallationModeUserContinuous {
		t.Fatalf("ParseInstallationMode() = %q", mode)
	}
	if !mode.UserScoped() {
		t.Fatal("user-continuous mode must keep ordinary-user file permissions")
	}
	if !mode.Continuous() {
		t.Fatal("user-continuous mode must remain active without a login session")
	}
}
