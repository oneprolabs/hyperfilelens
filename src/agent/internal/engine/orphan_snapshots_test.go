package engine

import "testing"

func TestOrphanInventoryRequiresCompleteExactIdentities(t *testing.T) {
	valid := `[{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","labels":{"type":"snapshot","path":"/backup"}}]`
	rows, err := parseOrphanManifestInventory(valid)
	if err != nil || len(rows) != 1 {
		t.Fatalf("valid inventory: %v", err)
	}
	for _, input := range []string{"null", "{}", `[{"id":"--all","labels":{"type":"snapshot"}}]`, `[{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","labels":{"type":"policy"}}]`, valid[:len(valid)-1]} {
		if _, err := parseOrphanManifestInventory(input); err == nil {
			t.Fatalf("accepted invalid inventory %q", input)
		}
	}
	rows, err = parseOrphanManifestInventory("[]")
	if err != nil || len(rows) != 0 {
		t.Fatalf("empty complete inventory: %v", err)
	}
}
