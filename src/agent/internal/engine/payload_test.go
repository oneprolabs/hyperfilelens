package engine

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestNormalizeKind(t *testing.T) {
	if got := NormalizeKind("explorer.list"); got != "browse" {
		t.Fatalf("got %q", got)
	}
	if got := NormalizeKind("backup.run"); got != "backup" {
		t.Fatalf("got %q", got)
	}
	if got := NormalizeKind("backup.snapshot.create"); got != "backup.snapshot.create" {
		t.Fatalf("got %q", got)
	}
	if got := NormalizeKind("nas.mount"); got != "nas.mount" {
		t.Fatalf("got %q", got)
	}
	if got := NormalizeKind("source.test"); got != "nas.test" {
		t.Fatalf("got %q", got)
	}
	if got := NormalizeKind("snapshot.list"); got != "snapshot.list" {
		t.Fatalf("got %q", got)
	}
	if got := NormalizeKind("lens.snapshot.scope.resolve"); got != "lens.snapshot.scope.resolve" {
		t.Fatalf("got %q", got)
	}
	if got := NormalizeKind("lens.snapshot.browse"); got != "lens.snapshot.browse" {
		t.Fatalf("got %q", got)
	}
	if got := NormalizeKind("repo.policy.apply"); got != "repository.policy.apply" {
		t.Fatalf("got %q", got)
	}
	if got := NormalizeKind("path.stat"); got != "path.info" {
		t.Fatalf("got %q", got)
	}
	if got := NormalizeKind("path.usage"); got != "path.usage" {
		t.Fatalf("got %q", got)
	}
}

func TestParsePayloadArgs(t *testing.T) {
	p := ParsePayload(map[string]any{
		"path":        "/data",
		"config_file": "/tmp/repo.config",
		"args":        []any{"snapshot", "list"},
	})
	if p.Path != "/data" || p.ConfigFile != "/tmp/repo.config" {
		t.Fatalf("unexpected payload: %+v", p)
	}
	if len(p.Args) != 2 || p.Args[0] != "snapshot" {
		t.Fatalf("args: %v", p.Args)
	}
}

func TestParsePayloadSnapshot(t *testing.T) {
	p := ParsePayload(map[string]any{
		"snapshot_id": "abc123",
		"path":        "/restore/here",
	})
	if p.SnapshotID != "abc123" || p.Path != "/restore/here" {
		t.Fatalf("unexpected payload: %+v", p)
	}
}

func TestParsePayloadBrowseOptions(t *testing.T) {
	p := ParsePayload(map[string]any{
		"path":             "/data",
		"dirs_only":        true,
		"include_metadata": false,
		"limit":            float64(200),
		"cursor":           " 200 ",
	})
	if !p.DirsOnly {
		t.Fatalf("dirs_only was not parsed: %+v", p)
	}
	if p.IncludeMetadata {
		t.Fatalf("include_metadata was not parsed: %+v", p)
	}
	if p.Limit != 200 {
		t.Fatalf("limit = %d", p.Limit)
	}
	if p.Cursor != "200" {
		t.Fatalf("cursor = %q", p.Cursor)
	}
}

func TestPayloadIntValueRejectsFractionalAndTrailingInput(t *testing.T) {
	for _, value := range []any{1.5, "10items", "1.5"} {
		if _, ok := payloadIntValue(value); ok {
			t.Fatalf("expected invalid integer payload %v to be rejected", value)
		}
	}
}

func TestPayloadStringValue(t *testing.T) {
	if got := payloadStringValue(nil); got != "" {
		t.Fatalf("nil -> %q", got)
	}
	if got := payloadStringValue(" 1.0.0 "); got != "1.0.0" {
		t.Fatalf("string -> %q", got)
	}
}

func TestPathInfoFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "report.txt")
	if err := os.WriteFile(path, []byte("hello"), 0o600); err != nil {
		t.Fatal(err)
	}

	result := New(nil).Run(context.Background(), Command{
		ID:   "path-info-test",
		Kind: "path.info",
		Payload: map[string]any{
			"path": path,
		},
	}, nil)

	if result.Status != "success" {
		t.Fatalf("status=%s error=%s", result.Status, result.Error)
	}
	if result.Result["path_type"] != "file" || result.Result["is_dir"] != false {
		t.Fatalf("unexpected result: %#v", result.Result)
	}
	if result.Result["exists"] != true {
		t.Fatalf("expected exists=true: %#v", result.Result)
	}
}

func TestPathInfoSkipsMetadata(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "report.txt")
	if err := os.WriteFile(path, []byte("hello"), 0o600); err != nil {
		t.Fatal(err)
	}

	result := New(nil).Run(context.Background(), Command{
		ID:   "path-info-lite-test",
		Kind: "path.info",
		Payload: map[string]any{
			"path":             path,
			"include_metadata": false,
		},
	}, nil)

	if result.Status != "success" {
		t.Fatalf("status=%s error=%s", result.Status, result.Error)
	}
	if _, ok := result.Result["size"]; ok {
		t.Fatalf("size should be omitted when metadata is disabled: %#v", result.Result)
	}
	if _, ok := result.Result["mod_time"]; ok {
		t.Fatalf("mod_time should be omitted when metadata is disabled: %#v", result.Result)
	}
}
