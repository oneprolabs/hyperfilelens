package kopia

import "testing"

func TestParseRestoreProgressLine(t *testing.T) {
	line := "Processed 5 (1.4 GB) of 5 (1.8 GB) 291.1 MB/s (75.2%) remaining 12s."
	snapshot, ok := ParseRestoreProgressLine(line)
	if !ok {
		t.Fatalf("expected restore progress line to parse")
	}
	if snapshot.Phase != "restoring" {
		t.Fatalf("expected restoring phase, got %q", snapshot.Phase)
	}
	if snapshot.ProcessedBytes <= 0 || snapshot.TotalBytes <= 0 {
		t.Fatalf("expected byte counters, got processed=%d total=%d", snapshot.ProcessedBytes, snapshot.TotalBytes)
	}
	if snapshot.Percent < 70 || snapshot.Percent > 80 {
		t.Fatalf("expected percent around 75, got %d", snapshot.Percent)
	}
	if snapshot.SpeedBytesPerSec <= 0 {
		t.Fatalf("expected speed, got %d", snapshot.SpeedBytesPerSec)
	}
	if snapshot.KopiaEtaSeconds != 12 {
		t.Fatalf("expected eta 12, got %d", snapshot.KopiaEtaSeconds)
	}
}

func TestParseProgressLineEstimatedAndEta(t *testing.T) {
	line := "* 0 hashing, 1234 hashed (1.2 GB), uploaded 500 MB, estimated 1.7 GB (29.4%) 12m30s left"
	snapshot, ok := ParseProgressLine(line)
	if !ok {
		t.Fatalf("expected progress line to parse")
	}
	if snapshot.EstimatedBytes <= 0 {
		t.Fatalf("expected estimated bytes, got %d", snapshot.EstimatedBytes)
	}
	if snapshot.Percent < 29 || snapshot.Percent > 30 {
		t.Fatalf("expected percent from estimated clause, got %d", snapshot.Percent)
	}
	if snapshot.KopiaEtaSeconds != 750 {
		t.Fatalf("expected 750 eta seconds, got %d", snapshot.KopiaEtaSeconds)
	}
	payload := ProgressPayload(snapshot)
	if payload["phase"] != "kopia_transfer" {
		t.Fatalf("expected kopia_transfer phase, got %#v", payload["phase"])
	}
	if payload["bytes_total_known"] != true {
		t.Fatalf("expected bytes_total_known true, got %#v", payload["bytes_total_known"])
	}
}
