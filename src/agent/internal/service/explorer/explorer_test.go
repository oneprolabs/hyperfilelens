package explorer

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestNormalizeMountPathWindows(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("windows-only mount path normalization")
	}
	cases := map[string]string{
		`C:`:       `C:\`,
		`C:.`:      `C:\`,
		`d:`:       `D:\`,
		`E:\`:      `E:\`,
		`C:\Users`: `C:\Users`,
		`D:\data`:  `D:\data`,
	}
	for input, want := range cases {
		if got := NormalizeMountPath(input); got != want {
			t.Fatalf("NormalizeMountPath(%q) = %q, want %q", input, got, want)
		}
	}
}

func TestListMountPoints(t *testing.T) {
	res, err := NewService().ListMountPoints(context.Background(), ListOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Entries) == 0 {
		t.Fatalf("expected at least one mount point, got %+v", res.Entries)
	}
	for _, entry := range res.Entries {
		if entry.Path == "" || !entry.IsDir {
			t.Fatalf("unexpected mount entry: %+v", entry)
		}
	}
}

func TestListLocalWithOptionsDirsOnlyNoMetadata(t *testing.T) {
	root := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, "dir-a"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "file-a.txt"), []byte("data"), 0o644); err != nil {
		t.Fatal(err)
	}

	res, err := NewService().ListLocalWithOptions(context.Background(), root, ListOptions{
		DirsOnly:        true,
		IncludeMetadata: false,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Entries) != 1 {
		t.Fatalf("entries = %+v", res.Entries)
	}
	got := res.Entries[0]
	if got.Name != "dir-a" || !got.IsDir {
		t.Fatalf("unexpected entry: %+v", got)
	}
	if got.Size != 0 || got.ModTime != "" {
		t.Fatalf("metadata should be empty when disabled: %+v", got)
	}
}

func TestListLocalWithOptionsTreatsSymlinkToDirectoryAsDirectory(t *testing.T) {
	root := t.TempDir()
	realDir := filepath.Join(root, "real-dir")
	if err := os.Mkdir(realDir, 0o755); err != nil {
		t.Fatal(err)
	}
	realFile := filepath.Join(root, "real-file.txt")
	if err := os.WriteFile(realFile, []byte("data"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(realDir, filepath.Join(root, "link-dir")); err != nil {
		t.Skipf("symlink not available: %v", err)
	}
	if err := os.Symlink(realFile, filepath.Join(root, "link-file")); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(root, "missing"), filepath.Join(root, "broken-link")); err != nil {
		t.Fatal(err)
	}

	res, err := NewService().ListLocalWithOptions(context.Background(), root, ListOptions{
		DirsOnly:        true,
		IncludeMetadata: false,
	})
	if err != nil {
		t.Fatal(err)
	}
	entries := entriesByName(res.Entries)
	if len(entries) != 2 {
		t.Fatalf("entries = %+v", res.Entries)
	}
	for _, name := range []string{"real-dir", "link-dir"} {
		entry, ok := entries[name]
		if !ok || !entry.IsDir {
			t.Fatalf("expected %s as directory, entries=%+v", name, res.Entries)
		}
	}
	if _, ok := entries["link-file"]; ok {
		t.Fatalf("symlink to file should not be listed as a directory: %+v", res.Entries)
	}
	if _, ok := entries["broken-link"]; ok {
		t.Fatalf("broken symlink should not be listed as a directory: %+v", res.Entries)
	}
}

func TestListLocalWithOptionsKeepsFilesByDefault(t *testing.T) {
	root := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, "dir-a"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "file-a.txt"), []byte("data"), 0o644); err != nil {
		t.Fatal(err)
	}

	res, err := NewService().ListLocalWithOptions(context.Background(), root, ListOptions{IncludeMetadata: true})
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Entries) != 2 {
		t.Fatalf("entries = %+v", res.Entries)
	}
	foundFile := false
	for _, entry := range res.Entries {
		if entry.Name == "file-a.txt" {
			foundFile = true
			if entry.IsDir || entry.Size != 4 || entry.ModTime == "" {
				t.Fatalf("unexpected file metadata: %+v", entry)
			}
		}
	}
	if !foundFile {
		t.Fatalf("file entry missing: %+v", res.Entries)
	}
}

func TestPathReadableRejectsMissingPath(t *testing.T) {
	root := t.TempDir()
	if !pathReadable(root) {
		t.Fatalf("existing directory should be readable: %s", root)
	}
	if pathReadable(filepath.Join(root, "missing")) {
		t.Fatal("missing path must not be reported as readable")
	}
}

func TestListLocalWithOptionsLimit(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{"a", "b", "c"} {
		if err := os.Mkdir(filepath.Join(root, name), 0o755); err != nil {
			t.Fatal(err)
		}
	}

	res, err := NewService().ListLocalWithOptions(context.Background(), root, ListOptions{
		DirsOnly:        true,
		IncludeMetadata: false,
		Limit:           2,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Entries) != 2 {
		t.Fatalf("entries = %+v", res.Entries)
	}
	if !res.HasMore {
		t.Fatalf("expected has_more for limited listing")
	}
}

func TestListLocalWithOptionsCursor(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{"a.txt", "b.txt", "c.txt"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte("data"), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	first, err := NewService().ListLocalWithOptions(context.Background(), root, ListOptions{
		IncludeMetadata: false,
		Limit:           2,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(first.Entries) != 2 || !first.HasMore || first.NextCursor == "" {
		t.Fatalf("unexpected first page: %+v", first)
	}

	second, err := NewService().ListLocalWithOptions(context.Background(), root, ListOptions{
		IncludeMetadata: false,
		Limit:           2,
		Cursor:          first.NextCursor,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(second.Entries) != 1 || second.HasMore || second.NextCursor != "" {
		t.Fatalf("unexpected second page: %+v", second)
	}
	if second.Entries[0].Path == first.Entries[0].Path || second.Entries[0].Path == first.Entries[1].Path {
		t.Fatalf("cursor returned duplicate entry: first=%+v second=%+v", first.Entries, second.Entries)
	}
}

func entriesByName(entries []Entry) map[string]Entry {
	out := make(map[string]Entry, len(entries))
	for _, entry := range entries {
		out[entry.Name] = entry
	}
	return out
}
