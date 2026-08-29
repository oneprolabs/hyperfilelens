package install

import (
	"errors"
	"fmt"
	"testing"
)

func TestShouldRetainDetachedLifecycleFiles(t *testing.T) {
	if ShouldRetainDetachedLifecycleFiles(nil) {
		t.Fatal("nil error must not retain staged files")
	}
	if ShouldRetainDetachedLifecycleFiles(errors.New("launcher failed")) {
		t.Fatal("ordinary launcher errors must not retain staged files")
	}
	owned := fmt.Errorf("start detached lifecycle: %w", errDetachedRunnerMayOwnFiles)
	if !ShouldRetainDetachedLifecycleFiles(owned) {
		t.Fatal("wrapped staged-file ownership errors must retain files")
	}
}
