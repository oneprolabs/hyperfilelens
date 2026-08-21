package config

import (
	"path/filepath"
	"testing"
)

func TestPathWithinRootRejectsPrefixCollision(t *testing.T) {
	root := filepath.Join(string(filepath.Separator), "opt", "hyperfilelens-agent")
	if !pathWithinRoot(filepath.Join(root, "hfl-agent"), root) {
		t.Fatal("installed executable should be within install root")
	}
	if pathWithinRoot(filepath.Join(root+"-old", "hfl-agent"), root) {
		t.Fatal("prefix-colliding install root should be rejected")
	}
}
