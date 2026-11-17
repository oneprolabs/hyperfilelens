package kopialayout

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestPrivateRuntimeIdentityIsAtomicAndConfined(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(RuntimeDir(root), "storage-identities", "lease")
	path := filepath.Join(dir, "identity.json")
	if err := PreparePrivateRuntimeDir(root, dir); err != nil {
		t.Fatal(err)
	}
	for _, value := range []string{"first-test-identity", "renewed-test-identity"} {
		if err := WritePrivateRuntimeFile(root, path, []byte(value)); err != nil {
			t.Fatal(err)
		}
		got, err := os.ReadFile(path)
		if err != nil || string(got) != value {
			t.Fatalf("identity replacement failed: %v", err)
		}
	}
	if runtime.GOOS != "windows" {
		info, err := os.Stat(path)
		if err != nil || info.Mode().Perm() != 0600 {
			t.Fatalf("identity file is not owner-only: %v", err)
		}
	}
	if err := WritePrivateRuntimeFile(root, filepath.Join(root, "outside.json"), []byte("no")); err == nil {
		t.Fatal("runtime identity escaped the managed runtime")
	}
	if err := PreparePrivateRuntimeDir(root, filepath.Join(root, "outside")); err == nil {
		t.Fatal("runtime directory escaped the managed runtime")
	}
}
