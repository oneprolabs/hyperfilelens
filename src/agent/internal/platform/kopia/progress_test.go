package kopia

import "testing"

func TestParseProgressLineHandlesKopiaStatus(t *testing.T) {
	line := "* 2 0 hashing, 1234 hashed (1.2 GB), 56 cached (10 MB), uploaded 789 (500 MB)"
	snapshot, ok := ParseProgressLine(line)
	if !ok {
		t.Fatalf("expected progress line to parse")
	}
	if snapshot.Phase != "uploading" {
		t.Fatalf("expected uploading phase, got %q", snapshot.Phase)
	}
	if snapshot.HashedBytes <= 0 || snapshot.UploadedBytes <= 0 {
		t.Fatalf("expected byte counters, got hashed=%d uploaded=%d", snapshot.HashedBytes, snapshot.UploadedBytes)
	}
	if snapshot.Percent < 45 || snapshot.Percent > 99 {
		t.Fatalf("expected upload-phase percent in [45,99], got %d", snapshot.Percent)
	}
}

func TestParseProgressLineTreatsPlainBytesAsUploadedBytes(t *testing.T) {
	line := "| 0 hashing, 1093 hashed (53 KB), 0 cached (0 B), uploaded 204 B (8 fatal errors), estimating..."
	snapshot, ok := ParseProgressLine(line)
	if !ok {
		t.Fatalf("expected progress line to parse")
	}
	if snapshot.Phase != "uploading" {
		t.Fatalf("expected uploading phase, got %q", snapshot.Phase)
	}
	if snapshot.UploadedBytes != 204 {
		t.Fatalf("expected 204 uploaded bytes, got %d", snapshot.UploadedBytes)
	}
	if snapshot.UploadedCount != 0 {
		t.Fatalf("expected no uploaded object count, got %d", snapshot.UploadedCount)
	}

	payload := ProgressPayload(snapshot)
	if payload["uploaded_bytes"] != int64(204) || payload["uploaded_count"] != int64(0) {
		t.Fatalf("expected byte-based upload payload, got %#v", payload)
	}
}

func TestParseProgressLineHandlesPercentAndCarriageReturn(t *testing.T) {
	line := "\r\u001b[2K 37.5% hashing..."
	snapshot, ok := ParseProgressLine(line)
	if !ok {
		t.Fatalf("expected percent progress line to parse")
	}
	if snapshot.Percent != 38 {
		t.Fatalf("expected rounded percent 38, got %d", snapshot.Percent)
	}
}

func TestProgressPayloadIncludesKopiaPercent(t *testing.T) {
	payload := ProgressPayload(ProgressSnapshot{
		Phase:         "uploading",
		Percent:       72,
		UploadedBytes: 500 * 1024 * 1024,
	})
	if payload["kopia_percent"] != 72 || payload["percent"] != 72 {
		t.Fatalf("expected percent fields in payload, got %#v", payload)
	}
	if payload["phase"] != "kopia_transfer" {
		t.Fatalf("expected kopia_transfer phase, got %#v", payload["phase"])
	}
}

func TestTransferBytesUploadingDoesNotUseHashedWhenCaughtUp(t *testing.T) {
	size := int64(13_100_000_000)
	done, total, known := transferBytes(ProgressSnapshot{
		Phase:         "uploading",
		UploadedBytes: size,
		HashedBytes:   size,
	})
	if done != size {
		t.Fatalf("expected done %d, got %d", size, done)
	}
	if known || total != 0 {
		t.Fatalf("expected unknown total when upload caught hashed, got total=%d known=%v", total, known)
	}
}

func TestTransferBytesUploadingUsesEstimatedTotal(t *testing.T) {
	done, total, known := transferBytes(ProgressSnapshot{
		Phase:          "uploading",
		UploadedBytes:  500 * 1000 * 1000,
		HashedBytes:    1_200 * 1000 * 1000,
		EstimatedBytes: 1_700 * 1000 * 1000,
	})
	if !known || total != 1_700*1000*1000 {
		t.Fatalf("expected estimated total, got total=%d known=%v", total, known)
	}
	if done != 500*1000*1000 {
		t.Fatalf("expected uploaded bytes as done, got %d", done)
	}
}

func TestParseProgressLineParsesSpeed(t *testing.T) {
	line := "* 0 hashing, 1234 hashed (1.2 GB), uploaded 500 MB, estimated 1.7 GB (29.4%) 12m30s left 25.5 MB/s"
	snapshot, ok := ParseProgressLine(line)
	if !ok {
		t.Fatalf("expected progress line to parse")
	}
	if snapshot.SpeedBytesPerSec <= 0 {
		t.Fatalf("expected speed bytes/sec, got %d", snapshot.SpeedBytesPerSec)
	}
	payload := ProgressPayload(snapshot)
	if payload["speed_bps"] == nil {
		t.Fatalf("expected speed_bps in payload, got %#v", payload)
	}
}
