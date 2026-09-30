package engine

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"hyperfilelens/agent/internal/platform/process"
)

// snapshotWorkspaceEntry is derived from the target snapshot, never from the
// existing workspace. File names are deliberately treated as case-sensitive.
type snapshotWorkspaceEntry struct {
	name string
	dir  bool
}

const maxWorkspaceEntriesPerDirectory = 250000

// reconcileSnapshotWorkspace removes only entries absent from an authoritative
// snapshot directory listing. SourceLens sidecars for retained files stay in
// place so a non-forced conversion can reuse their content fingerprints.
//
// The caller must verify that root is an HFL-owned managed workspace and that
// list reads the pinned snapshot. Each directory is compared independently:
// a large workspace does not require a whole-tree in-memory file inventory.
func reconcileSnapshotWorkspace(
	ctx context.Context,
	root string,
	list func(context.Context, string) ([]snapshotWorkspaceEntry, error),
	progress ...func(int),
) (int, error) {
	return reconcileSnapshotWorkspaceWithMarker(ctx, root, list, false, progress...)
}

func reconcileSnapshotWorkspaceWithMarker(
	ctx context.Context,
	root string,
	list func(context.Context, string) ([]snapshotWorkspaceEntry, error),
	preserveRootMarker bool,
	progress ...func(int),
) (int, error) {
	root = filepath.Clean(root)
	if !filepath.IsAbs(root) || root == string(filepath.Separator) {
		return 0, fmt.Errorf("managed workspace root is invalid")
	}
	info, err := os.Lstat(root)
	if err != nil {
		return 0, err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return 0, fmt.Errorf("managed workspace root is not a regular directory")
	}
	processed := 0
	onRemoved := func() {
		processed++
		if len(progress) > 0 && progress[0] != nil && processed%1000 == 0 {
			progress[0](processed)
		}
	}
	removed, err := reconcileSnapshotDirectory(ctx, root, "", list, preserveRootMarker, onRemoved)
	if len(progress) > 0 && progress[0] != nil {
		progress[0](processed)
	}
	return removed, err
}

func reconcileSnapshotDirectory(
	ctx context.Context,
	root, rel string,
	list func(context.Context, string) ([]snapshotWorkspaceEntry, error),
	preserveRootMarker bool,
	onRemoved func(),
) (int, error) {
	if err := ctx.Err(); err != nil {
		return 0, err
	}
	expectedRows, err := list(ctx, rel)
	if err != nil {
		return 0, err
	}
	if len(expectedRows) > maxWorkspaceEntriesPerDirectory {
		return 0, fmt.Errorf("snapshot directory is too large to reconcile safely")
	}
	expected := make(map[string]snapshotWorkspaceEntry, len(expectedRows))
	for _, entry := range expectedRows {
		if entry.name == "" || entry.name == "." || entry.name == ".." ||
			strings.ContainsAny(entry.name, `/\`) ||
			strings.HasSuffix(entry.name, ".sourcelens") ||
			strings.HasPrefix(entry.name, ".sourcelens") {
			return 0, fmt.Errorf("snapshot contains an unsafe or reserved path")
		}
		if _, exists := expected[entry.name]; exists {
			return 0, fmt.Errorf("snapshot contains duplicate file names")
		}
		expected[entry.name] = entry
	}
	dir := filepath.Join(root, filepath.FromSlash(rel))
	fd, cleanDir, err := secureOpenDirectory(
		dir, root, true, uint64(os.O_RDONLY),
	)
	if err != nil {
		return 0, err
	}
	handle := secureDirectoryFile(fd, cleanDir)
	if handle == nil {
		return 0, fmt.Errorf("managed workspace reconciliation requires Linux")
	}
	defer handle.Close()
	existing := []os.DirEntry{}
	for {
		if err := ctx.Err(); err != nil {
			return 0, err
		}
		batch, readErr := handle.ReadDir(8192)
		existing = append(existing, batch...)
		if len(existing) > maxWorkspaceEntriesPerDirectory {
			return 0, fmt.Errorf("workspace directory is too large to reconcile safely")
		}
		if readErr != nil {
			if readErr == io.EOF {
				break
			}
			return 0, readErr
		}
	}
	actual := make(map[string]os.DirEntry, len(existing))
	for _, entry := range existing {
		actual[entry.Name()] = entry
	}
	// Verify retained source entries before removing anything in this folder.
	for name, entry := range expected {
		found, ok := actual[name]
		if !ok || found.Type()&os.ModeSymlink != 0 || found.IsDir() != entry.dir {
			return 0, fmt.Errorf("restored snapshot file is missing or has a different type")
		}
	}
	removed := 0
	for _, entry := range existing {
		if err := ctx.Err(); err != nil {
			return removed, err
		}
		name := entry.Name()
		if _, keep := expected[name]; keep {
			continue
		}
		// HFL/SourceLens metadata is not source data. Per-file sidecars are
		// retained only while their source file exists in the new snapshot.
		if preserveRootMarker && rel == "" && name == ".sourcelens-datasource.json" {
			continue
		}
		if strings.HasPrefix(name, ".sourcelens") {
			return removed, fmt.Errorf("unknown SourceLens workspace metadata")
		}
		if strings.HasSuffix(name, ".sourcelens") {
			if _, keep := expected[strings.TrimSuffix(name, ".sourcelens")]; keep {
				continue
			}
		}
		relativeEntry := name
		if rel != "" {
			relativeEntry = filepath.Join(filepath.FromSlash(rel), name)
		}
		if err := removeReconciledWorkspaceEntry(root, relativeEntry, onRemoved); err != nil {
			return removed, err
		}
		removed++
	}
	for _, entry := range expectedRows {
		if !entry.dir {
			continue
		}
		child := entry.name
		if rel != "" {
			child = rel + "/" + child
		}
		count, err := reconcileSnapshotDirectory(ctx, root, child, list, preserveRootMarker, onRemoved)
		removed += count
		if err != nil {
			return removed, err
		}
	}
	return removed, nil
}

func reconcileRestoredSnapshotDirectory(
	ctx context.Context,
	bin, configFile string,
	env map[string]string,
	snapshotID, selectedPath, target, managedWorkspace string,
	report func(int),
) (int, error) {
	scanned := 0
	list := func(ctx context.Context, relative string) ([]snapshotWorkspaceEntry, error) {
		path := strings.Trim(strings.Trim(selectedPath, "/")+"/"+relative, "/")
		entries := []snapshotWorkspaceEntry{}
		var invalid error
		_, runErr := process.RunStreamingDiscardStdout(
			ctx, bin,
			[]string{
				"--config-file=" + configFile, "ls", "-l",
				snapshotObjectPath(snapshotID, path),
			},
			env, "",
			func(line string, stderr bool) {
				if stderr || strings.TrimSpace(line) == "" || invalid != nil {
					return
				}
				if len(entries) >= maxWorkspaceEntriesPerDirectory {
					invalid = fmt.Errorf("snapshot directory is too large to reconcile safely")
					return
				}
				mode, _, _, name, ok := parseSnapshotBrowseLongLine(line)
				if !ok {
					invalid = fmt.Errorf("invalid snapshot listing")
					return
				}
				entryType, downloadable, _ := snapshotBrowseEntryType(mode, "")
				if !downloadable || (entryType != "file" && entryType != "dir") {
					invalid = fmt.Errorf("snapshot contains unsupported content")
					return
				}
				entries = append(entries, snapshotWorkspaceEntry{
					name: name,
					dir:  entryType == "dir",
				})
				scanned++
				if report != nil && scanned%5000 == 0 {
					report(scanned)
				}
			},
		)
		if invalid != nil {
			return nil, invalid
		}
		if runErr != nil {
			return nil, fmt.Errorf("snapshot listing failed")
		}
		if report != nil {
			report(scanned)
		}
		return entries, nil
	}
	return reconcileSnapshotWorkspaceWithMarker(
		ctx, target, list,
		filepath.Clean(target) == filepath.Clean(managedWorkspace),
		report,
	)
}
