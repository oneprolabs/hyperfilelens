package kopia

import (
	"encoding/json"
	"math"
	"regexp"
	"strconv"
	"strings"
)

var (
	ansiEscapePattern      = regexp.MustCompile(`\x1b\[[0-9;]*[a-zA-Z]`)
	hashedPattern          = regexp.MustCompile(`(?i)(\d+)\s+hashed\s+\(([^)]+)\)`)
	cachedPattern          = regexp.MustCompile(`(?i)(\d+)\s+cached\s+\(([^)]+)\)`)
	uploadedPattern        = regexp.MustCompile(`(?i)uploaded\s+(\d+)(?:\s+\(([^)]+)\))?`)
	uploadedBytesPattern   = regexp.MustCompile(`(?i)uploaded\s+\(([^)]+)\)`)
	uploadedDecimalPattern = regexp.MustCompile(`(?i)uploaded\s+([\d.]+\s*(?:[KMGT]i?B|B))`)
	hashingPattern         = regexp.MustCompile(`(?i)(\d+)\s+hashing`)
	percentPattern         = regexp.MustCompile(`(\d+(?:\.\d+)?)\s*%`)
	estimatedPattern       = regexp.MustCompile(`(?i)estimated\s+([\d.]+\s*(?:[KMGT]i?B))\s*(?:\((\d+(?:\.\d+)?)\s*%\))?`)
	etaLeftPattern         = regexp.MustCompile(`(?i)(?:^|\s)((?:\d+(?:\.\d+)?[hms])+)\s+left`)
)

// ProgressSnapshot captures parsed Kopia snapshot progress counters.
type ProgressSnapshot struct {
	SchemaVersion    int
	Sequence         int64
	SampledAt        string
	Phase            string
	Percent          int
	PercentValue     float64
	PercentKnown     bool
	ProcessedBytes   int64
	HashingCount     int64
	HashedCount      int64
	HashedBytes      int64
	CachedCount      int64
	CachedBytes      int64
	UploadedCount    int64
	UploadedBytes    int64
	EstimatedBytes   int64
	EstimatedKnown   bool
	SpeedBytesPerSec int64
	KopiaEtaSeconds  int64
	KopiaEtaKnown    bool
	Line             string
}

// NormalizeProgressLine strips ANSI escapes and keeps the latest carriage-return segment.
func NormalizeProgressLine(raw string) string {
	line := ansiEscapePattern.ReplaceAllString(raw, "")
	if idx := strings.LastIndex(line, "\r"); idx >= 0 {
		line = line[idx+1:]
	}
	return strings.TrimSpace(line)
}

// ParseProgressLine extracts progress counters from one Kopia stderr/stdout line.
func ParseProgressLine(raw string) (ProgressSnapshot, bool) {
	if snapshot, ok := parseStructuredProgressLine(raw); ok {
		return snapshot, true
	}

	line := NormalizeProgressLine(raw)
	if line == "" {
		return ProgressSnapshot{}, false
	}

	snapshot := ProgressSnapshot{Line: line}
	if match := percentPattern.FindStringSubmatch(line); len(match) == 2 {
		if value, err := strconv.ParseFloat(match[1], 64); err == nil {
			snapshot.Percent = clampPercent(int(math.Round(value)))
			snapshot.PercentValue = value
			snapshot.PercentKnown = true
		}
	}
	if match := hashingPattern.FindStringSubmatch(line); len(match) == 2 {
		snapshot.HashingCount, _ = strconv.ParseInt(match[1], 10, 64)
	}
	if match := hashedPattern.FindStringSubmatch(line); len(match) == 3 {
		snapshot.HashedCount, _ = strconv.ParseInt(match[1], 10, 64)
		snapshot.HashedBytes = parseHumanSize(match[2])
	}
	if match := cachedPattern.FindStringSubmatch(line); len(match) == 3 {
		snapshot.CachedCount, _ = strconv.ParseInt(match[1], 10, 64)
		snapshot.CachedBytes = parseHumanSize(match[2])
	}
	if match := uploadedDecimalPattern.FindStringSubmatch(line); len(match) == 2 {
		snapshot.UploadedBytes = parseHumanSize(match[1])
	} else if match := uploadedPattern.FindStringSubmatch(line); len(match) >= 2 {
		snapshot.UploadedCount, _ = strconv.ParseInt(match[1], 10, 64)
		if len(match) >= 3 && match[2] != "" {
			snapshot.UploadedBytes = parseHumanSize(match[2])
		}
	} else if match := uploadedBytesPattern.FindStringSubmatch(line); len(match) == 2 {
		snapshot.UploadedBytes = parseHumanSize(match[1])
	}
	if match := estimatedPattern.FindStringSubmatch(line); len(match) >= 2 {
		estimatedRaw := strings.TrimSpace(match[1])
		if humanSizeHasUnit(estimatedRaw) {
			snapshot.EstimatedBytes = parseHumanSize(estimatedRaw)
			snapshot.EstimatedKnown = true
		}
		if len(match) >= 3 && match[2] != "" {
			if value, err := strconv.ParseFloat(match[2], 64); err == nil {
				snapshot.Percent = clampPercent(int(math.Round(value)))
				snapshot.PercentValue = value
				snapshot.PercentKnown = true
			}
		}
	}
	if speedMatch := restoreSpeedPattern.FindStringSubmatch(line); len(speedMatch) == 3 {
		snapshot.SpeedBytesPerSec = speedFromRate(speedMatch[1], speedMatch[2])
	}
	if match := etaLeftPattern.FindStringSubmatch(line); len(match) >= 2 {
		if value := parseDurationToken(strings.TrimSpace(match[1])); value >= 0 {
			snapshot.KopiaEtaSeconds = value
			snapshot.KopiaEtaKnown = true
		}
	}

	if snapshot.Percent == 0 {
		hasCounters := snapshot.HashingCount > 0 ||
			snapshot.HashedCount > 0 ||
			snapshot.HashedBytes > 0 ||
			snapshot.UploadedCount > 0 ||
			snapshot.UploadedBytes > 0
		if !hasCounters {
			switch {
			case strings.Contains(strings.ToLower(line), "snapshotting"):
				snapshot.Percent = 2
			case strings.Contains(strings.ToLower(line), "processing"):
				snapshot.Percent = 3
			}
		}
	}

	if snapshot.Percent == 0 {
		snapshot.Percent = estimatePercent(snapshot)
		if snapshot.Percent > 0 {
			snapshot.PercentValue = float64(snapshot.Percent)
			snapshot.PercentKnown = true
		}
	}
	if snapshot.Percent <= 0 &&
		snapshot.HashingCount == 0 &&
		snapshot.HashedCount == 0 &&
		snapshot.UploadedCount == 0 &&
		snapshot.UploadedBytes == 0 &&
		snapshot.HashedBytes == 0 {
		return ProgressSnapshot{}, false
	}

	switch {
	case snapshot.UploadedBytes > 0 || snapshot.UploadedCount > 0:
		snapshot.Phase = "uploading"
	case snapshot.HashingCount > 0 || snapshot.HashedBytes > 0 || snapshot.HashedCount > 0:
		snapshot.Phase = "hashing"
	default:
		snapshot.Phase = "running"
	}
	snapshot.SchemaVersion = 1
	snapshot.ProcessedBytes = snapshot.CachedBytes + snapshot.HashedBytes
	return snapshot, true
}

func parseStructuredProgressLine(raw string) (ProgressSnapshot, bool) {
	var event struct {
		Type             string   `json:"type"`
		SchemaVersion    int      `json:"schema_version"`
		Sequence         int64    `json:"sequence"`
		SampledAt        string   `json:"sampled_at"`
		Phase            string   `json:"phase"`
		ProcessedBytes   int64    `json:"processed_bytes"`
		EstimatedBytes   *int64   `json:"estimated_bytes"`
		UploadedBytes    int64    `json:"uploaded_bytes"`
		PercentComplete  *float64 `json:"percent_complete"`
		RemainingSeconds *int64   `json:"remaining_seconds"`
	}
	if err := json.Unmarshal([]byte(strings.TrimSpace(raw)), &event); err != nil {
		return ProgressSnapshot{}, false
	}
	if event.Type != "hfl_snapshot_progress" || event.SchemaVersion != 2 || event.Sequence <= 0 ||
		event.ProcessedBytes < 0 || event.UploadedBytes < 0 {
		return ProgressSnapshot{}, false
	}
	switch event.Phase {
	case "estimating", "processing", "finalizing", "done":
	default:
		return ProgressSnapshot{}, false
	}

	snapshot := ProgressSnapshot{
		SchemaVersion:  event.SchemaVersion,
		Sequence:       event.Sequence,
		SampledAt:      event.SampledAt,
		Phase:          event.Phase,
		ProcessedBytes: event.ProcessedBytes,
		UploadedBytes:  event.UploadedBytes,
	}
	if event.EstimatedBytes != nil {
		if *event.EstimatedBytes < 0 {
			return ProgressSnapshot{}, false
		}
		snapshot.EstimatedBytes = *event.EstimatedBytes
		snapshot.EstimatedKnown = true
	}
	if event.PercentComplete != nil {
		if math.IsNaN(*event.PercentComplete) || math.IsInf(*event.PercentComplete, 0) ||
			*event.PercentComplete < 0 || *event.PercentComplete > 100 {
			return ProgressSnapshot{}, false
		}
		snapshot.PercentValue = *event.PercentComplete
		snapshot.Percent = int(math.Round(*event.PercentComplete))
		snapshot.PercentKnown = true
	}
	if event.RemainingSeconds != nil {
		if *event.RemainingSeconds < 0 {
			return ProgressSnapshot{}, false
		}
		snapshot.KopiaEtaSeconds = *event.RemainingSeconds
		snapshot.KopiaEtaKnown = true
	}
	return snapshot, true
}

// ProgressPayload converts a parsed snapshot into an agent task.progress payload.
func ProgressPayload(snapshot ProgressSnapshot) map[string]any {
	return ProgressPayloadWithSpeed(snapshot, 0, "")
}

// ProgressPayloadWithSpeed enriches payload with optional computed throughput.
func ProgressPayloadWithSpeed(snapshot ProgressSnapshot, speedBps int64, speedSource string) map[string]any {
	return ProgressPayloadWithDualSpeed(snapshot, speedBps, speedSource, 0, "")
}

// ProgressPayloadWithDualSpeed reports hash and upload throughput separately.
func ProgressPayloadWithDualSpeed(
	snapshot ProgressSnapshot,
	hashSpeedBps int64,
	hashSpeedSource string,
	uploadSpeedBps int64,
	uploadSpeedSource string,
) map[string]any {
	bytesDone, bytesTotal, totalKnown := transferBytes(snapshot)
	schemaVersion := snapshot.SchemaVersion
	if schemaVersion <= 0 {
		schemaVersion = 1
	}
	payload := map[string]any{
		"progress_schema_version": schemaVersion,
		"phase":                   "kopia_transfer",
		"kopia_phase":             snapshot.Phase,
		"processed_bytes":         bytesDone,
		"hashing_count":           snapshot.HashingCount,
		"hashed_count":            snapshot.HashedCount,
		"hashed_bytes":            snapshot.HashedBytes,
		"cached_count":            snapshot.CachedCount,
		"cached_bytes":            snapshot.CachedBytes,
		"uploaded_count":          snapshot.UploadedCount,
		"uploaded_bytes":          snapshot.UploadedBytes,
		"estimated_bytes":         snapshot.EstimatedBytes,
		"bytes_done":              bytesDone,
		"bytes_total":             bytesTotal,
		"bytes_total_known":       totalKnown,
	}
	if snapshot.PercentKnown {
		payload["kopia_percent"] = snapshot.PercentValue
		payload["percent"] = snapshot.PercentValue
	} else if schemaVersion == 1 {
		payload["kopia_percent"] = snapshot.Percent
		payload["percent"] = snapshot.Percent
	}
	if snapshot.SampledAt != "" {
		payload["sampled_at"] = snapshot.SampledAt
	}
	if snapshot.Sequence > 0 {
		payload["progress_sequence"] = snapshot.Sequence
	}
	if hashSpeedSource != "" {
		payload["processing_speed_bps"] = hashSpeedBps
		payload["processing_speed_source"] = hashSpeedSource
		// Keep the v1 field while mixed Backend versions are supported.
		payload["hash_speed_bps"] = hashSpeedBps
		payload["hash_speed_source"] = hashSpeedSource
	}
	if uploadSpeedSource != "" {
		payload["upload_speed_bps"] = uploadSpeedBps
		payload["upload_speed_source"] = uploadSpeedSource
	}
	legacySpeed := uploadSpeedBps
	legacySource := uploadSpeedSource
	if legacySpeed <= 0 && schemaVersion == 1 && hashSpeedBps > 0 {
		legacySpeed = hashSpeedBps
		legacySource = hashSpeedSource
	}
	if legacySource != "" {
		payload["speed_bps"] = legacySpeed
		if legacySource != "" {
			payload["speed_source"] = legacySource
		}
	} else if snapshot.SpeedBytesPerSec > 0 {
		payload["speed_bps"] = snapshot.SpeedBytesPerSec
		payload["speed_source"] = "kopia"
	}
	if snapshot.KopiaEtaKnown || snapshot.KopiaEtaSeconds > 0 {
		payload["kopia_eta_seconds"] = snapshot.KopiaEtaSeconds
	}
	if snapshot.Line != "" {
		payload["progress_text"] = snapshot.Line
	}
	return payload
}

func transferBytes(snapshot ProgressSnapshot) (done int64, total int64, totalKnown bool) {
	done = snapshot.ProcessedBytes
	if done == 0 {
		done = snapshot.CachedBytes + snapshot.HashedBytes
	}
	if snapshot.EstimatedKnown {
		return done, snapshot.EstimatedBytes, true
	}
	if snapshot.EstimatedBytes > 0 && credibleBytesTotal(done, snapshot.EstimatedBytes) {
		return done, snapshot.EstimatedBytes, true
	}
	return done, 0, false
}

func parseDurationToken(raw string) int64 {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return 0
	}
	var total int64
	for len(raw) > 0 {
		idx := 1
		for idx < len(raw) && (raw[idx] >= '0' && raw[idx] <= '9' || raw[idx] == '.') {
			idx++
		}
		if idx >= len(raw) {
			break
		}
		number, err := strconv.ParseFloat(raw[:idx], 64)
		if err != nil || number < 0 {
			break
		}
		unit := raw[idx]
		switch unit {
		case 'h':
			total += int64(number * 3600)
		case 'm':
			total += int64(number * 60)
		case 's':
			total += int64(number)
		}
		raw = raw[idx+1:]
	}
	return total
}

func estimatePercent(snapshot ProgressSnapshot) int {
	if snapshot.UploadedBytes > 0 && snapshot.HashedBytes > 0 {
		ratio := float64(snapshot.UploadedBytes) / float64(snapshot.HashedBytes)
		if ratio > 1 {
			ratio = 1
		}
		return clampPercent(45 + int(math.Round(ratio*50)))
	}
	if snapshot.UploadedCount > 0 && snapshot.HashedCount > 0 {
		ratio := float64(snapshot.UploadedCount) / float64(snapshot.HashedCount)
		if ratio > 1 {
			ratio = 1
		}
		return clampPercent(45 + int(math.Round(ratio*50)))
	}
	if snapshot.HashedBytes > 0 {
		// Hashing phase: grow slowly with processed bytes so large backups still move.
		score := 5 + int(math.Log10(float64(snapshot.HashedBytes)+1)*12)
		return clampPercent(minInt(score, 44))
	}
	if snapshot.HashingCount > 0 || snapshot.HashedCount > 0 {
		return 5
	}
	return 0
}

func parseHumanSize(raw string) int64 {
	value := strings.TrimSpace(strings.ToUpper(raw))
	if value == "" {
		return 0
	}
	multiplier := int64(1)
	switch {
	case strings.HasSuffix(value, "KB"):
		multiplier = 1000
		value = strings.TrimSuffix(value, "KB")
	case strings.HasSuffix(value, "MB"):
		multiplier = 1000 * 1000
		value = strings.TrimSuffix(value, "MB")
	case strings.HasSuffix(value, "GB"):
		multiplier = 1000 * 1000 * 1000
		value = strings.TrimSuffix(value, "GB")
	case strings.HasSuffix(value, "TB"):
		multiplier = 1000 * 1000 * 1000 * 1000
		value = strings.TrimSuffix(value, "TB")
	case strings.HasSuffix(value, "KIB"):
		multiplier = 1024
		value = strings.TrimSuffix(value, "KIB")
	case strings.HasSuffix(value, "MIB"):
		multiplier = 1024 * 1024
		value = strings.TrimSuffix(value, "MIB")
	case strings.HasSuffix(value, "GIB"):
		multiplier = 1024 * 1024 * 1024
		value = strings.TrimSuffix(value, "GIB")
	case strings.HasSuffix(value, "TIB"):
		multiplier = 1024 * 1024 * 1024 * 1024
		value = strings.TrimSuffix(value, "TIB")
	case strings.HasSuffix(value, "B"):
		value = strings.TrimSuffix(value, "B")
	}
	value = strings.TrimSpace(value)
	number, err := strconv.ParseFloat(value, 64)
	if err != nil || number < 0 {
		return 0
	}
	return int64(number * float64(multiplier))
}

func clampPercent(value int) int {
	if value < 0 {
		return 0
	}
	if value > 99 {
		return 99
	}
	return value
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
