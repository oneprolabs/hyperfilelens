//go:build !windows

package lifecycle

import "testing"

func TestAccountReadinessTrustsOnlySafeRootOwnedAnchor(t *testing.T) {
	if !trustedOwner(0, 1001, true, 0755) || !trustedOwner(1001, 1001, false, 0700) {
		t.Fatal("supported account installation layout was rejected")
	}
	if trustedOwner(0, 1001, false, 0700) || trustedOwner(0, 1001, true, 0775) ||
		trustedOwner(1002, 1001, true, 0755) || trustedOwner(1001, 1001, false, 0777) {
		t.Fatal("unsafe runtime owner or permissions were accepted")
	}
}
