package explorer

import (
	"context"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"

	"github.com/shirou/gopsutil/v4/disk"
)

// Entry describes a file or directory in a local listing.
type Entry struct {
	Name    string `json:"name"`
	Path    string `json:"path"`
	IsDir   bool   `json:"is_dir"`
	Size    int64  `json:"size,omitempty"`
	ModTime string `json:"mod_time,omitempty"`
}

// ListOptions controls how much data a local listing should collect.
type ListOptions struct {
	DirsOnly         bool
	IncludeMetadata  bool
	Limit            int
	Cursor           string
	LocalFixedOnly   bool
	FilterUnreadable bool
}

// ListResult is the bounded local listing response.
type ListResult struct {
	Entries    []Entry
	HasMore    bool
	NextCursor string
}

// Service lists local paths (repository browse uses Kopia via task payload args).
type Service struct{}

// NewService returns an explorer service instance.
func NewService() *Service {
	return &Service{}
}

// DefaultRoot returns a stable filesystem root for interactive browsing.
func DefaultRoot() string {
	if runtime.GOOS == "windows" {
		if wd, err := os.Getwd(); err == nil {
			if volume := filepath.VolumeName(wd); volume != "" {
				return volume + `\`
			}
		}
		return `C:\`
	}
	return string(filepath.Separator)
}

// ListMountPoints returns filesystem mount points for top-level disk browsing.
func (s *Service) ListMountPoints(ctx context.Context, opts ListOptions) (ListResult, error) {
	_ = s
	if err := ctx.Err(); err != nil {
		return ListResult{}, err
	}
	partitions, err := disk.Partitions(false)
	if err != nil {
		return ListResult{}, err
	}
	seen := make(map[string]struct{})
	out := make([]Entry, 0, len(partitions))
	offset := cursorOffset(opts.Cursor)
	matched := 0
	appendEntry := func(mountpoint, device string) bool {
		mountpoint = normalizeMountPath(mountpoint)
		if mountpoint == "" || mountpoint == "." {
			return false
		}
		if !mountPointAllowed(mountpoint, opts.LocalFixedOnly) ||
			(opts.FilterUnreadable && !pathReadable(mountpoint)) {
			return false
		}
		key := mountKey(mountpoint)
		if _, ok := seen[key]; ok {
			return false
		}
		seen[key] = struct{}{}
		entry := Entry{
			Name:  mountLabel(mountpoint, device),
			Path:  mountpoint,
			IsDir: true,
		}
		if opts.IncludeMetadata {
			if usage, usageErr := disk.Usage(mountpoint); usageErr == nil {
				entry.Size = int64(usage.Total)
			}
		}
		if matched < offset {
			matched++
			return false
		}
		out = append(out, entry)
		matched++
		return opts.Limit > 0 && len(out) >= opts.Limit
	}

	for _, part := range partitions {
		if appendEntry(part.Mountpoint, part.Device) {
			return ListResult{Entries: out, HasMore: true, NextCursor: strconv.Itoa(matched)}, nil
		}
	}
	for _, mountpoint := range extraMountPoints(seen) {
		if appendEntry(mountpoint, mountpoint) {
			return ListResult{Entries: out, HasMore: true, NextCursor: strconv.Itoa(matched)}, nil
		}
	}
	sort.Slice(out, func(i, j int) bool {
		left := mountSortKey(out[i].Path)
		right := mountSortKey(out[j].Path)
		if left == right {
			return out[i].Path < out[j].Path
		}
		return left < right
	})
	return ListResult{Entries: out, HasMore: false}, nil
}

func cursorOffset(cursor string) int {
	offset, err := strconv.Atoi(strings.TrimSpace(cursor))
	if err != nil || offset < 0 {
		return 0
	}
	return offset
}

func mountKey(path string) string {
	if runtime.GOOS == "windows" {
		return strings.ToLower(filepath.Clean(path))
	}
	return filepath.Clean(path)
}

func mountSortKey(path string) string {
	if runtime.GOOS == "windows" {
		return strings.ToLower(filepath.VolumeName(path))
	}
	return path
}

func mountLabel(mountpoint, device string) string {
	if runtime.GOOS == "windows" {
		volume := filepath.VolumeName(mountpoint)
		if volume != "" {
			return volume + `\`
		}
	}
	name := filepath.Base(strings.TrimRight(mountpoint, string(filepath.Separator)))
	if name == "" || name == string(filepath.Separator) {
		return mountpoint
	}
	if device != "" && device != name {
		return name
	}
	return name
}

func entryIsDir(root string, item os.DirEntry) bool {
	if item.IsDir() {
		return true
	}
	if item.Type()&os.ModeSymlink == 0 {
		return false
	}
	info, err := os.Stat(filepath.Join(root, item.Name()))
	return err == nil && info.IsDir()
}

// ListLocal returns directory entries under path.
func (s *Service) ListLocal(ctx context.Context, path string) ([]Entry, error) {
	res, err := s.ListLocalWithOptions(ctx, path, ListOptions{IncludeMetadata: true})
	if err != nil {
		return nil, err
	}
	return res.Entries, nil
}

// ListLocalWithOptions returns directory entries under path without recursively
// walking descendants. It can skip files and metadata for fast UI directory trees.
func (s *Service) ListLocalWithOptions(ctx context.Context, path string, opts ListOptions) (ListResult, error) {
	_ = s
	if err := ctx.Err(); err != nil {
		return ListResult{}, err
	}
	if path == "" {
		path = DefaultRoot()
	}
	root := normalizeMountPath(path)
	dir, err := os.Open(root)
	if err != nil {
		return ListResult{}, err
	}
	defer dir.Close()

	batchSize := 128
	if opts.Limit > 0 && opts.Limit < batchSize {
		batchSize = opts.Limit
	}
	out := make([]Entry, 0)
	hasMore := false
	offset := cursorOffset(opts.Cursor)
	matched := 0
	for {
		if err := ctx.Err(); err != nil {
			return ListResult{}, err
		}
		items, readErr := dir.ReadDir(batchSize)
		if readErr != nil && readErr != io.EOF {
			return ListResult{}, readErr
		}
		for _, item := range items {
			isDir := entryIsDir(root, item)
			if opts.DirsOnly && !isDir {
				continue
			}
			entryPath := filepath.Join(root, item.Name())
			if opts.FilterUnreadable && !pathReadable(entryPath) {
				continue
			}
			if matched < offset {
				matched++
				continue
			}
			e := Entry{
				Name:  item.Name(),
				Path:  entryPath,
				IsDir: isDir,
			}
			if opts.IncludeMetadata {
				if info, infoErr := item.Info(); infoErr == nil {
					e.Size = info.Size()
					e.ModTime = info.ModTime().UTC().Format("2006-01-02T15:04:05Z07:00")
				}
			}
			out = append(out, e)
			matched++
			if opts.Limit > 0 && len(out) >= opts.Limit {
				hasMore = readErr != io.EOF
				nextCursor := ""
				if hasMore {
					nextCursor = strconv.Itoa(matched)
				}
				return ListResult{Entries: out, HasMore: hasMore, NextCursor: nextCursor}, nil
			}
		}
		if readErr == io.EOF {
			break
		}
	}
	return ListResult{Entries: out, HasMore: hasMore}, nil
}

func pathReadable(path string) bool {
	handle, err := os.Open(path)
	if err != nil {
		return false
	}
	return handle.Close() == nil
}

// ListRepository is reserved for Kopia-backed listing (use task payload args today).
func (s *Service) ListRepository(ctx context.Context, kopiaPath, snapshotID, path string) ([]Entry, error) {
	_ = s
	_ = ctx
	_ = kopiaPath
	_ = snapshotID
	_ = path
	return nil, fs.ErrInvalid
}
