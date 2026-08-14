package wire

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestBoundTaskResultDropsLargeCommandOutputAndKeepsSnapshotIdentity(t *testing.T) {
	result := map[string]any{
		"kopia_snapshot_id": "snapshot-1",
		"size_bytes":        int64(42),
		"snapshot_create": map[string]any{
			"stdout":      strings.Repeat("o", maxTaskResultBytes),
			"stderr":      strings.Repeat("e", maxTaskResultBytes),
			"stdout_tail": "final stdout",
			"stderr_tail": "final stderr",
		},
	}
	bounded, stats := boundTaskResult(result)
	encoded, err := json.Marshal(bounded)
	if err != nil {
		t.Fatal(err)
	}
	if len(encoded) > maxTaskResultBytes || !stats.Truncated {
		t.Fatalf("bytes=%d stats=%+v", len(encoded), stats)
	}
	if bounded["kopia_snapshot_id"] != "snapshot-1" || bounded["size_bytes"] != int64(42) {
		t.Fatalf("essential fields lost: %#v", bounded)
	}
	command := bounded["snapshot_create"].(map[string]any)
	if _, ok := command["stdout"]; ok {
		t.Fatal("full stdout was retained")
	}
	if command["stderr_tail"] != "final stderr" {
		t.Fatalf("diagnostic tail lost: %#v", command)
	}
}

func TestBoundTaskResultFallbackIsDeterministic(t *testing.T) {
	largeStats := map[string]any{}
	for i := 0; i < 100; i++ {
		largeStats[string(rune('a'+i%26))+strings.Repeat("k", i)] = strings.Repeat("v", 32*1024)
	}
	result := map[string]any{
		"kopia_snapshot_id": "snapshot-2",
		"last_progress":     largeStats,
		"other":             strings.Repeat("x", maxTaskResultBytes),
	}
	first, _ := boundTaskResult(result)
	second, _ := boundTaskResult(result)
	a, _ := json.Marshal(first)
	b, _ := json.Marshal(second)
	if string(a) != string(b) {
		t.Fatal("bounded result is not deterministic across retries")
	}
	if len(a) > maxTaskResultBytes || first["kopia_snapshot_id"] != "snapshot-2" {
		t.Fatalf("bounded result invalid: bytes=%d result=%#v", len(a), first)
	}
}

func TestTaskResultFrameStaysWithinWireLimit(t *testing.T) {
	frame := NewTaskResult(
		strings.Repeat("t", 128),
		"success",
		map[string]any{
			"kopia_snapshot_id": "snapshot-3",
			"other":             strings.Repeat("x", maxTaskResultFrameBytes),
		},
		strings.Repeat("e", maxResultStringBytes*2),
	)
	encoded, err := json.Marshal(frame)
	if err != nil {
		t.Fatal(err)
	}
	if len(encoded) > maxTaskResultFrameBytes {
		t.Fatalf("task.result frame bytes=%d, want <=%d", len(encoded), maxTaskResultFrameBytes)
	}
}
