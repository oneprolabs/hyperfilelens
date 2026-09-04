package kopia

import "testing"

func TestParseEntrySummary(t *testing.T) {
	tests := []struct {
		name      string
		raw       string
		wantOK    bool
		wantType  string
		wantSize  int64
		wantItems int64
	}{
		{
			name:      "complete directory",
			raw:       `{"version":1,"path_type":"directory","size_bytes":8388608,"file_count":5,"directory_count":2,"symlink_count":1,"summary_available":true,"complete":true}`,
			wantOK:    true,
			wantType:  "directory",
			wantSize:  8388608,
			wantItems: 8,
		},
		{
			name:      "incomplete directory remains usable for type detection",
			raw:       `{"version":1,"path_type":"directory","summary_available":true,"complete":false,"incomplete_reason":"incomplete snapshot"}`,
			wantOK:    true,
			wantType:  "directory",
			wantItems: 0,
		},
		{
			name:      "file",
			raw:       `{"version":1,"path_type":"file","size_bytes":12,"file_count":1,"complete":true}`,
			wantOK:    true,
			wantType:  "file",
			wantSize:  12,
			wantItems: 1,
		},
		{name: "missing summary cannot be complete", raw: `{"version":1,"path_type":"directory","complete":true}`, wantOK: false},
		{name: "negative count", raw: `{"version":1,"path_type":"directory","file_count":-1}`, wantOK: false},
		{name: "unknown version", raw: `{"version":2,"path_type":"file","file_count":1,"complete":true}`, wantOK: false},
		{name: "invalid json", raw: `{`, wantOK: false},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			summary, ok := ParseEntrySummary(test.raw)
			if ok != test.wantOK {
				t.Fatalf("ParseEntrySummary() ok = %v, want %v", ok, test.wantOK)
			}
			if !ok {
				return
			}
			if summary.PathType != test.wantType || summary.SizeBytes != test.wantSize || summary.TotalCount() != test.wantItems {
				t.Fatalf("ParseEntrySummary() = %#v, want type=%q size=%d items=%d", summary, test.wantType, test.wantSize, test.wantItems)
			}
		})
	}
}
