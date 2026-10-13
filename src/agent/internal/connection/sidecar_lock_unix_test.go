//go:build !windows

package connection

import (
	"context"
	"path/filepath"
	"testing"
	"time"
)

func TestSidecarLockSerializesBackgroundRefreshAndHonorsCancellation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sidecar.lock")
	release, err := lockSidecarAt(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if second, err := lockSidecarAt(ctx, path); err == nil {
		second()
		t.Fatal("background sidecar work was admitted during maintenance")
	}
	release()
	second, err := lockSidecarAt(context.Background(), path)
	if err != nil {
		t.Fatal("lock was not released after maintenance", err)
	}
	second()
}
