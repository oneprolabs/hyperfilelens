package monitor

import (
	"context"
	"testing"
	"time"
)

func TestSampleOnceReturnsPayload(t *testing.T) {
	sample, err := NewCollector().SampleOnce(context.Background())
	if err != nil {
		t.Fatalf("SampleOnce: %v", err)
	}
	payload := sample.ToPayload()
	if payload["cpu"] == nil {
		t.Fatal("expected cpu payload")
	}
	if payload["memory"] == nil {
		t.Fatal("expected memory payload")
	}
	if _, ok := payload["cpu_usage"]; !ok {
		t.Fatal("expected cpu_usage scalar")
	}
}

func TestToPayloadDoesNotFabricateUnavailableMetricsAsZero(t *testing.T) {
	sample := Sample{
		Timestamp:   time.Unix(1, 0).UTC(),
		CPU:         map[string]any{},
		Memory:      map[string]any{},
		Swap:        map[string]any{},
		Disks:       []any{},
		Networks:    []any{},
		Unavailable: []string{"cpu_usage", "memory", "disk_usage"},
	}
	payload := sample.ToPayload()
	for _, key := range []string{
		"cpu_usage",
		"memory_usage",
		"swap_usage",
		"disk_usage",
		"network_rx",
		"network_tx",
	} {
		if _, ok := payload[key]; ok {
			t.Fatalf("unavailable metric %s must not be reported as zero", key)
		}
	}
	metadata, ok := payload["metadata"].(map[string]any)
	if !ok {
		t.Fatal("expected monitor metadata")
	}
	if metadata["collection_status"] != "partial" {
		t.Fatalf("collection_status = %v", metadata["collection_status"])
	}
}
