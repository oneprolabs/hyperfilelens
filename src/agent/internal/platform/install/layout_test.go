package install

import "testing"

func TestPathAllowedForRemoval(t *testing.T) {
	allowed := []string{
		"/opt/hyperfilelens-agent",
		"/opt/hyperfilelens-agent/backup",
		"/var/lib/hyperfilelens-agent",
		"/var/lib/hyperfilelens-agent/backup/state",
	}
	for _, path := range allowed {
		if !PathAllowedForRemoval(path) {
			t.Fatalf("expected allowed: %q", path)
		}
	}
	denied := []string{
		"",
		"/opt/other-agent",
		"/tmp/hyperfilelens-agent",
		"/var/lib/hyperfilelens-agent/../../../etc",
		"/opt/hyperfilelens-agent/../../etc",
		"var/lib/hyperfilelens-agent",
	}
	for _, path := range denied {
		if PathAllowedForRemoval(path) {
			t.Fatalf("expected denied: %q", path)
		}
	}
}
