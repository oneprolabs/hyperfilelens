package disk

import (
	"path/filepath"
	"runtime"
	"testing"
)

func TestPathWithinMount(t *testing.T) {
	if runtime.GOOS == "windows" {
		mount := comparablePath(`C:\`)
		if !pathWithinMount(comparablePath(`C:\data\repository`), mount) {
			t.Fatal("expected repository path to belong to the drive root")
		}
		if pathWithinMount(comparablePath(`D:\data`), mount) {
			t.Fatal("did not expect a different drive to match")
		}
		return
	}

	mount := filepath.Clean("/var/lib")
	if !pathWithinMount(filepath.Clean("/var/lib/hyperfilelens/repository"), mount) {
		t.Fatal("expected nested path to belong to mount")
	}
	if pathWithinMount(filepath.Clean("/var/library"), mount) {
		t.Fatal("did not expect a sibling path with the same prefix to match")
	}
}

func TestStoragePoolKeyPrefersDeviceForDeduplication(t *testing.T) {
	first := storagePoolKey("/dev/sdb1", "/mnt/repository-a")
	second := storagePoolKey("/dev/sdb1", "/mnt/repository-b")
	if first != second {
		t.Fatalf("expected bind mounts on one filesystem to share a pool key: %q != %q", first, second)
	}
	if first != "device:/dev/sdb1" {
		t.Fatalf("unexpected pool key: %q", first)
	}
}
