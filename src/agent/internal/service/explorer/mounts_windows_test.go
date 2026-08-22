//go:build windows

package explorer

import (
	"path/filepath"
	"testing"
)

func TestMountPointAllowedKeepsOnlyLocalFixedDrives(t *testing.T) {
	root := filepath.VolumeName(t.TempDir()) + `\`
	if !mountPointAllowed(root, true) {
		t.Fatalf("temporary directory drive should be a local fixed drive: %s", root)
	}
	if mountPointAllowed(`\\server\share`, true) {
		t.Fatal("UNC share must not be exposed by a current-user local-drive listing")
	}
	if !mountPointAllowed(`\\server\share`, false) {
		t.Fatal("system-mode mount discovery should retain its existing behavior")
	}
}
