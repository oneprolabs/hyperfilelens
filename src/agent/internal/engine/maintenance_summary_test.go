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
