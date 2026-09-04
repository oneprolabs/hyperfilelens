package engine

import (
	"testing"
	"time"
)

func TestMaintenanceSummaryFromInfoUsesCurrentCycle(t *testing.T) {
	startedAt := time.Date(2026, 9, 2, 7, 43, 40, 0, time.UTC)
	info := `{
  "schedule": {
    "runs": {
      "snapshot-gc": [
        {
          "start": "2026-09-01T04:00:00Z",
          "success": true,
          "extra": [{"kind":"snapshotGCStats","data":{"deletedContentCount":999}}]
        },
        {
          "start": "2026-09-02T07:43:41Z",
          "success": true,
          "extra": [{"kind":"snapshotGCStats","data":{
            "unreferencedContentCount":55,
            "unreferencedContentSize":3145728,
            "deletedContentCount":55,
            "deletedContentSize":3145728,
            "unreferencedRecentContentCount":7214,
            "unreferencedRecentContentSize":7408351232,
            "inUseContentCount":78672,
            "inUseContentSize":1288490188
          }}]
        }
      ],
      "full-delete-blobs": [{
        "start": "2026-09-02T07:43:50Z",
        "success": true,
        "extra": [{"kind":"deleteUnreferencedPacksStats","data":{
          "unreferencedPackCount":12,
          "unreferencedTotalSize":12000,
          "deletedPackCount":10,
          "deletedTotalSize":10000,
          "retainedPackCount":2,
          "retainedTotalSize":2000
        }}]
      }]
    }
  }
}`

	summary := maintenanceSummaryFromInfo(info, "full", startedAt)
	if summary == nil {
		t.Fatal("expected a maintenance summary")
	}
	content, ok := summary["content_gc"].(map[string]any)
	if !ok || content["deleted_count"] != uint64(55) || content["deferred_count"] != uint64(7214) {
		t.Fatalf("unexpected content summary: %#v", summary["content_gc"])
	}
	packs, ok := summary["pack_gc"].(map[string]any)
	if !ok || packs["deleted_bytes"] != uint64(10000) {
		t.Fatalf("unexpected pack summary: %#v", summary["pack_gc"])
	}
}

func TestMaintenanceSummaryFromInfoDoesNotReuseOldPackGC(t *testing.T) {
	startedAt := time.Date(2026, 9, 2, 7, 43, 40, 0, time.UTC)
	info := `{"schedule":{"runs":{
    "snapshot-gc":[{"start":"2026-09-02T07:43:41Z","success":true,"extra":[{"kind":"snapshotGCStats","data":{"inUseContentCount":3}}]}],
    "full-delete-blobs":[{"start":"2026-09-01T04:00:00Z","success":true,"extra":[{"kind":"deleteUnreferencedPacksStats","data":{"deletedPackCount":99}}]}]
  }}}`

	summary := maintenanceSummaryFromInfo(info, "full", startedAt)
	if summary == nil {
		t.Fatal("expected a maintenance summary")
	}
	if packs, ok := summary["pack_gc"].(map[string]any); ok && packs != nil {
		t.Fatalf("expected no current pack GC data, got %#v", summary["pack_gc"])
	}
}

func TestMaintenanceSummaryFallsBackToApproximateStderr(t *testing.T) {
	stderr := `Running full maintenance...
GC found 55 unused contents (3 MB)
GC found 7214 unused contents that are too recent to delete (6.9 GB)
GC found 78672 in-use contents (1.2 GB)
GC found 341 in-use system-contents (223.6 KB)
GC undeleted 0 contents (0 B)`

	summary := buildMaintenanceSummary("not-json", stderr, "full", time.Now())
	if summary == nil || summary["source"] != "stderr" || summary["approximate"] != true {
		t.Fatalf("unexpected fallback summary: %#v", summary)
	}
	content := summary["content_gc"].(map[string]any)
	if content["deleted_bytes"] != uint64(3<<20) || content["deferred_count"] != uint64(7214) {
		t.Fatalf("unexpected fallback content: %#v", content)
	}
	if summary["pack_gc"] != nil {
		t.Fatalf("stderr fallback must not claim physical pack deletion: %#v", summary)
	}
}

func TestQuickMaintenanceSummaryReportsStandardStagesWithoutInventingStatistics(t *testing.T) {
	startedAt := time.Date(2026, 9, 3, 1, 0, 0, 0, time.UTC)
	info := `{"schedule":{"runs":{
    "quick-rewrite-contents":[{"start":"2026-09-03T01:00:01Z","success":true,"extra":[{"kind":"rewriteContentsStats","data":{"toRewriteContentCount":0,"toRewriteContentSize":0,"rewrittenContentCount":0,"rewrittenContentSize":0,"retainedContentCount":0,"retainedContentSize":0}}]}],
    "index-compaction":[{"start":"2026-09-03T01:00:02Z","success":true,"extra":[]}],
    "cleanup-logs":[{"start":"2026-09-03T01:00:03Z","success":true,"extra":[{"kind":"cleanupLogsStats","data":{"deletedBlobCount":0,"deletedBlobSize":0,"retainedBlobCount":12,"retainedBlobSize":4096}}]}]
  }}}`

	summary := maintenanceSummaryFromInfo(info, "quick", startedAt)
	if summary == nil {
		t.Fatal("expected a Quick Maintenance summary")
	}
	stages := summary["stages"].([]map[string]any)
	if len(stages) != 4 {
		t.Fatalf("expected four standard Quick stages, got %#v", stages)
	}
	if stages[0]["type"] != "content_rewrite" || stages[0]["statistics_available"] != true {
		t.Fatalf("unexpected rewrite stage: %#v", stages[0])
	}
	rewriteMetrics := stages[0]["metrics"].(map[string]any)
	if value, ok := rewriteMetrics["rewritten_count"]; !ok || value != uint64(0) {
		t.Fatalf("expected a reported zero rewrite count, got %#v", rewriteMetrics)
	}
	if stages[1]["type"] != "pack_gc" || stages[1]["status"] != "not_run" {
		t.Fatalf("expected Pack GC to be explicitly not run, got %#v", stages[1])
	}
	if stages[2]["status"] != "completed" || stages[2]["statistics_available"] != false {
		t.Fatalf("expected completed index compaction without quantitative statistics, got %#v", stages[2])
	}
	cleanupMetrics := stages[3]["metrics"].(map[string]any)
	if cleanupMetrics["deleted_count"] != uint64(0) || cleanupMetrics["retained_bytes"] != uint64(4096) {
		t.Fatalf("unexpected log cleanup metrics: %#v", cleanupMetrics)
	}
}

func TestQuickMaintenanceSummaryReportsEpochStages(t *testing.T) {
	startedAt := time.Date(2026, 9, 3, 2, 0, 0, 0, time.UTC)
	info := `{"schedule":{"runs":{
    "compact-single-epoch":[{"start":"2026-09-03T02:00:01Z","success":true,"extra":[{"kind":"compactSingleEpochStats","data":{"supersededIndexBlobCount":9,"supersededIndexTotalSize":170917888,"epoch":42}}]}],
    "advance-epoch":[{"start":"2026-09-03T02:00:02Z","success":true,"extra":[{"kind":"advanceEpochStats","data":{"currentEpoch":43,"wasAdvanced":false}}]}]
  }}}`

	summary := maintenanceSummaryFromInfo(info, "quick", startedAt)
	stages := summary["stages"].([]map[string]any)
	if len(stages) != 2 || stages[0]["type"] != "epoch_compaction" || stages[1]["type"] != "epoch_advance" {
		t.Fatalf("unexpected Epoch Quick stages: %#v", stages)
	}
	advanceMetrics := stages[1]["metrics"].(map[string]any)
	if advanceMetrics["current_epoch"] != uint64(43) || advanceMetrics["advanced"] != false {
		t.Fatalf("unexpected Epoch advance metrics: %#v", advanceMetrics)
	}
}

func TestQuickMaintenanceSummaryIgnoresOldStageRuns(t *testing.T) {
	startedAt := time.Date(2026, 9, 3, 2, 0, 0, 0, time.UTC)
	info := `{"schedule":{"runs":{
    "cleanup-logs":[{"start":"2026-09-02T02:00:02Z","success":true,"extra":[{"kind":"cleanupLogsStats","data":{"deletedBlobCount":99}}]}]
  }}}`

	if summary := maintenanceSummaryFromInfo(info, "quick", startedAt); summary != nil {
		t.Fatalf("expected no summary from previous-cycle stages, got %#v", summary)
	}
}
