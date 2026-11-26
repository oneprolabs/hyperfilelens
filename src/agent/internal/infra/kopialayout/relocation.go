package kopialayout

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"time"

	"hyperfilelens/agent/internal/platform/disk"
	"hyperfilelens/agent/internal/platform/filelock"
	"hyperfilelens/agent/internal/platform/vfs"
)

const ownerFile = ".hfl-cache-owner.json"

type Validation struct {
	Token          string     `json:"validation_token"`
	Destination    string     `json:"destination"`
	ParentID       string     `json:"parent_identity"`
	Source         ActiveRoot `json:"source"`
	SourceID       string     `json:"source_identity"`
	PolicyRevision int64      `json:"policy_revision"`
	Expires        time.Time  `json:"expires_at"`
}
type Relocation struct {
	TaskID        string     `json:"task_id"`
	Stage         string     `json:"stage"`
	Validation    Validation `json:"validation"`
	DestinationID string     `json:"destination_identity,omitempty"`
	Error         string     `json:"error,omitempty"`
	// Connection originals can contain credentials. This private journal is NEVER
	// returned through status, progress or logs.
	Verified  bool                       `json:"verified"`
	Originals map[string]json.RawMessage `json:"originals,omitempty"`
}

func (j Relocation) Public() map[string]any {
	return map[string]any{"task_id": j.TaskID, "stage": j.Stage, "source": j.Validation.Source.Path, "destination": j.Validation.Destination, "error": j.Error}
}
func overlap(a, b string) bool { return samePath(a, b) || within(a, b) || within(b, a) }
func ownerID(root string) string {
	absolute, _ := filepath.Abs(root)
	return digest([]byte(absolute))[:24]
}
func completedLayout(root string) error {
	status, err := Status(root)
	if err != nil {
		return err
	}
	if status["disposable"] != true {
		return fmt.Errorf("cache layout migration must be completed and disposable before relocation")
	}
	return nil
}

// criticalCacheDestination rejects a filesystem root itself, but only rejects
// descendants of named system directories. A Windows root includes its volume
// (for example E:\), so comparing it with the path separator alone is incorrect.
func criticalCacheDestination(parent string) bool {
	for _, p := range []string{"/", "/etc", "/bin", "/sbin", "/usr", "/dev", "/proc", "/sys", "/boot", "/System", "/Windows", "/Program Files"} {
		critical := filepath.Clean(p)
		if volume := filepath.VolumeName(parent); volume != "" {
			critical = filepath.Join(volume, p)
		}
		if samePath(parent, critical) || (p != "/" && within(critical, parent)) {
			return true
		}
	}
	return false
}

// ValidateDestination does not accept a pending path switch or adopt user data.
// The user chooses a parent; only an installation-owned child can be managed.
func ValidateDestination(ctx context.Context, root, parent, logs string) (Validation, error) {
	var v Validation
	if err := completedLayout(root); err != nil {
		return v, err
	}
	if !filepath.IsAbs(parent) || len(parent) > 4096 {
		return v, fmt.Errorf("destination must be an absolute path")
	}
	parent = filepath.Clean(parent)
	defaultCache := vfs.AgentCacheDir(root)
	restoreDefault := samePath(parent, root) || samePath(parent, defaultCache)
	if restoreDefault {
		// Accept the default location even when the previous move removed it.
		// Root is only the parent, never the cache/cleanup target itself.
		parent = filepath.Clean(root)
	}
	physical, err := physicalPath(parent)
	if err != nil {
		return v, err
	}
	if !samePath(parent, physical) {
		return v, fmt.Errorf("destination aliases are not allowed")
	}
	if err := plainPath(filepath.VolumeName(parent)+string(filepath.Separator), parent); err != nil {
		return v, err
	}
	id, err := directoryIdentity(parent)
	if err != nil {
		return v, fmt.Errorf("destination parent must be an accessible existing directory: %w", err)
	}
	if criticalCacheDestination(parent) {
		return v, fmt.Errorf("critical system destination is not allowed")
	}
	dest := filepath.Join(parent, ".hyperfilelens-cache-"+ownerID(root))
	if restoreDefault {
		dest = defaultCache
	}
	protected := []string{root, logs, vfs.AgentDataStoreDir(root), vfs.AgentRuntimeDir(root)}
	configs, err := connectionConfigs(root)
	if err != nil {
		return v, err
	}
	for _, raw := range configs {
		var config struct {
			Storage struct {
				Type   string `json:"type"`
				Config struct {
					Path string `json:"path"`
				} `json:"config"`
			} `json:"storage"`
		}
		if json.Unmarshal(raw, &config) != nil {
			return v, fmt.Errorf("invalid managed connection")
		}
		if config.Storage.Type == "filesystem" {
			protected = append(protected, config.Storage.Config.Path)
		}
	}
	for index, p := range protected {
		// Only the exact canonical cache subtree is exempt from the broad
		// installation-root guard. Logs/data/runtime/repos remain protected,
		// including a repository or custom log root equal to the Agent root.
		if index == 0 && restoreDefault && samePath(dest, defaultCache) {
			continue
		}
		if p == "" {
			continue
		}
		physical, err := physicalPath(p)
		if err != nil {
			return v, err
		}
		if overlap(dest, physical) {
			return v, fmt.Errorf("destination overlaps protected installation, repository or log data")
		}
	}
	active, err := ReadActiveRoot(root)
	if err != nil {
		return v, err
	}
	if overlap(active.Path, dest) {
		return v, fmt.Errorf("old and new cache roots must be distinct and not nested")
	}
	sourceID, err := directoryIdentity(active.Path)
	if err != nil {
		return v, fmt.Errorf("active cache root must be accessible: %w", err)
	}
	if err := validateRelocationDestination(root, dest); err != nil {
		return v, err
	}
	_, _, available, err := disk.Usage(parent)
	if err != nil {
		return v, fmt.Errorf("destination space check failed: %w", err)
	}
	p, err := ReadPolicy(root)
	if err != nil {
		return v, err
	}
	if available < uint64(p.Blocking) {
		return v, fmt.Errorf("destination available bytes %d are below blocking threshold %d", available, p.Blocking)
	}
	f, err := os.CreateTemp(parent, ".hfl-cache-probe-")
	if err != nil {
		return v, err
	}
	name := f.Name()
	err = f.Close()
	removeErr := os.Remove(name)
	if err != nil {
		return v, err
	}
	if removeErr != nil {
		return v, removeErr
	}
	nonce := make([]byte, 32)
	if _, err := rand.Read(nonce); err != nil {
		return v, err
	}
	v = Validation{hex.EncodeToString(nonce), dest, id, active, sourceID, p.Revision, time.Now().UTC().Add(5 * time.Minute)}
	if err := saveJSON(root, managementPath(root, "validation"), v); err != nil {
		return v, err
	}
	return v, ctx.Err()
}

// validateRelocationDestination recognizes only the empty historical
// repositories scaffold at this installation's canonical default cache. It
// never changes general cache ownership checks or claims a marker during
// validation; relocation must repeat this check before initializing ownership.
func validateRelocationDestination(root, path string) error {
	ownershipErr := validateOwnedOrAbsent(root, path)
	if ownershipErr == nil {
		return nil
	}
	if !samePath(path, vfs.AgentCacheDir(root)) {
		return ownershipErr
	}
	if _, err := os.Lstat(filepath.Join(path, ownerFile)); !os.IsNotExist(err) {
		return ownershipErr // Existing, foreign, malformed and linked markers stay authoritative.
	}
	if err := completedLayout(root); err != nil {
		return ownershipErr
	}
	if err := plainPath(filepath.VolumeName(path)+string(filepath.Separator), path); err != nil {
		return ownershipErr
	}
	before, err := os.Lstat(path)
	if err != nil || !before.IsDir() || before.Mode()&os.ModeSymlink != 0 {
		return ownershipErr
	}
	anchored, err := os.OpenRoot(path)
	if err != nil {
		return ownershipErr
	}
	defer anchored.Close()
	directory, err := anchored.Open(".")
	if err != nil {
		return ownershipErr
	}
	defer directory.Close()
	opened, err := directory.Stat()
	if err != nil || !os.SameFile(before, opened) {
		return ownershipErr
	}
	entries, err := directory.ReadDir(2)
	if err != nil && err != io.EOF {
		return ownershipErr
	}
	name := filepath.Base(LegacyConfigDir(root))
	if len(entries) != 1 || entries[0].Name() != name || !entries[0].IsDir() {
		return ownershipErr
	}
	childInfo, err := anchored.Lstat(name)
	if err != nil || !childInfo.IsDir() || childInfo.Mode()&os.ModeSymlink != 0 {
		return ownershipErr
	}
	child, err := anchored.Open(name)
	if err != nil {
		return ownershipErr
	}
	defer child.Close()
	openedChild, err := child.Stat()
	if err != nil || !os.SameFile(childInfo, openedChild) {
		return ownershipErr
	}
	contents, readErr := child.ReadDir(1)
	if len(contents) != 0 || readErr != io.EOF {
		return ownershipErr
	}
	current, err := os.Lstat(path)
	if err != nil || !os.SameFile(before, current) || !current.IsDir() {
		return ownershipErr
	}
	return nil
}

func validateOwnedOrAbsent(root, path string) error {
	if err := plainPath(filepath.VolumeName(path)+string(filepath.Separator), path); err != nil {
		return err
	}
	if _, err := os.Lstat(path); os.IsNotExist(err) {
		return nil
	} else if err != nil {
		return err
	}
	var marker string
	if err := loadJSON(path, filepath.Join(path, ownerFile), &marker); err != nil || marker != ownerID(root) {
		// Only an empty dedicated child may be initialized after interruption
		// between mkdir and ownership checkpoint. Never adopt non-empty user data.
		if os.IsNotExist(err) {
			directory, e := os.Open(path)
			if e == nil {
				entries, readErr := directory.ReadDir(1)
				_ = directory.Close()
				if len(entries) == 0 && readErr == io.EOF {
					return nil
				}
			}
		}
		return fmt.Errorf("destination is not a verified installation-owned cache subtree")
	}
	return nil
}
func connectionConfigs(root string) (map[string]json.RawMessage, error) {
	result := map[string]json.RawMessage{}
	directory, err := os.Open(ConfigDir(root))
	if os.IsNotExist(err) {
		return result, nil
	}
	if err != nil {
		return nil, err
	}
	defer directory.Close()
	entries, err := directory.ReadDir(10001)
	if err == io.EOF {
		err = nil
	}
	if os.IsNotExist(err) {
		return result, nil
	}
	if err != nil {
		return nil, err
	}
	if len(entries) > 10000 {
		return nil, fmt.Errorf("connection inventory exceeds bound")
	}
	totalBytes := 0
	for _, entry := range entries {
		if !managedConfigName(entry.Name()) {
			continue
		}
		path := filepath.Join(ConfigDir(root), entry.Name())
		raw, err := readRegular(root, path, maxConfigBytes)
		if err != nil {
			return nil, err
		}
		if !json.Valid(raw) {
			return nil, fmt.Errorf("invalid managed connection")
		}
		totalBytes += len(raw) + len(path) + 1024
		if totalBytes > 16<<20 {
			return nil, fmt.Errorf("connection recovery journal would exceed safe size bound")
		}
		result[path] = raw
	}
	return result, nil
}
func revalidate(root string, v Validation, logs string) error {
	if err := completedLayout(root); err != nil {
		return err
	}
	active, err := ReadActiveRoot(root)
	if err != nil {
		return err
	}
	if active != v.Source {
		return fmt.Errorf("active cache root changed")
	}
	id, err := directoryIdentity(v.Source.Path)
	if err != nil || id != v.SourceID {
		return fmt.Errorf("source cache filesystem identity changed")
	}
	p, err := ReadPolicy(root)
	if err != nil || p.Revision != v.PolicyRevision {
		return fmt.Errorf("cache policy changed; validate destination again")
	}
	parent := filepath.Dir(v.Destination)
	id, err = directoryIdentity(parent)
	if err != nil || id != v.ParentID {
		return fmt.Errorf("destination filesystem identity changed")
	}
	fresh, err := ValidateDestination(context.Background(), root, parent, logs)
	if err != nil {
		return err
	}
	if fresh.Destination != v.Destination || fresh.ParentID != v.ParentID {
		return fmt.Errorf("destination changed")
	}
	return nil
}

// Relocate is serialized across processes. Intent closes admission before the
// shared usage lock drains; old processes are inspected again under exclusion.
func Relocate(ctx context.Context, root, logs, taskID, token string, progress func(map[string]any), verify func(string) error) (map[string]any, error) {
	if err := privateDir(root, managementDir(root)); err != nil {
		return nil, err
	}
	release, err := filelock.Acquire(ctx, filepath.Join(managementDir(root), "relocation.lock"), false)
	if err != nil {
		return nil, err
	}
	defer release()
	var j Relocation
	err = loadJSON(root, managementPath(root, "relocation"), &j)
	if err == nil && j.Stage != "completed" && j.Stage != "failed" && j.TaskID != taskID {
		return j.Public(), fmt.Errorf("another relocation or cleanup is pending")
	}
	if err != nil && !os.IsNotExist(err) {
		return nil, err
	}
	if j.TaskID != taskID || j.Stage == "completed" || j.Stage == "failed" {
		if j.TaskID == taskID {
			return j.Public(), fmt.Errorf("relocation task is already terminal")
		}
		var v Validation
		if err := loadJSON(root, managementPath(root, "validation"), &v); err != nil {
			return nil, err
		}
		if token == "" || v.Token != token || time.Now().After(v.Expires) {
			return nil, fmt.Errorf("destination validation expired or does not match")
		}
		if err := revalidate(root, v, logs); err != nil {
			return nil, err
		}
		j = Relocation{TaskID: taskID, Stage: "waiting_for_users", Validation: v}
	}
	if err := saveRelocation(root, j); err != nil {
		return j.Public(), err
	}
	if progress != nil {
		progress(j.Public())
	}
	return executeRelocation(ctx, root, logs, &j, progress, verify)
}
func executeRelocation(ctx context.Context, root, logs string, j *Relocation, progress func(map[string]any), verify func(string) error) (result map[string]any, err error) {
	var releaseUsage func()
	defer func() {
		if releaseUsage != nil {
			releaseUsage()
		}
	}()
	checkpoint := func(stage string) error {
		j.Stage = stage
		if e := saveRelocation(root, j); e != nil {
			return e
		}
		if progress != nil {
			progress(j.Public())
		}
		return nil
	}
	defer func() {
		if err == nil {
			return
		}
		a, _ := ReadActiveRoot(root)
		if j.Verified {
			j.Stage = "switched_cleanup_pending"
		} else {
			// Roll back partial config writes before reopening admission. On rollback
			// failure retain the switching journal and keep admission closed for repair.
			for path, raw := range j.Originals {
				if e := writeDurable(root, path, raw); e != nil {
					j.Error = "Connection rollback requires recovery."
					_ = saveRelocation(root, j)
					result = j.Public()
					return
				}
			}
			if a.Path == j.Validation.Destination {
				if rollbackErr := storeActive(root, j.Validation.Source); rollbackErr != nil {
					j.Error = "Active root rollback requires recovery."
					_ = saveRelocation(root, j)
					result = j.Public()
					return
				}
			}
			j.Stage = "failed"
		}
		j.Error = err.Error()
		_ = saveRelocation(root, j)
		result = j.Public()
	}()
	if err = privateDir(root, RuntimeDir(root)); err != nil {
		return nil, err
	}
	release, e := filelock.Acquire(ctx, filepath.Join(RuntimeDir(root), "migration.lock"), false)
	if e != nil {
		return nil, e
	}
	releaseUsage = release
	active, e := ReadActiveRoot(root)
	if e != nil {
		return nil, e
	}
	if err = CheckUsers(ctx, root, false); err != nil {
		return nil, err
	}
	if active.Path != j.Validation.Destination {
		// A crash before manifest commit may have left only some references
		// updated. Restore immutable originals before retrying, never replace the
		// recovery copies with a partially switched configuration.
		for path, raw := range j.Originals {
			if err = writeDurable(root, path, raw); err != nil {
				return nil, err
			}
		}
		if err = checkpoint("revalidating"); err != nil {
			return nil, err
		}
		if err = revalidate(root, j.Validation, logs); err != nil {
			return nil, err
		}
		if err = CheckUsers(ctx, root, false); err != nil {
			return nil, err
		}
		if err = checkpoint("preparing"); err != nil {
			return nil, err
		}
		if _, e := os.Lstat(j.Validation.Destination); os.IsNotExist(e) {
			if err = os.Mkdir(j.Validation.Destination, 0o700); err != nil {
				return nil, err
			}
		} else if e != nil {
			return nil, e
		} else if err = validateRelocationDestination(root, j.Validation.Destination); err != nil {
			return nil, err
		}
		if err = restrictPath(j.Validation.Destination, true); err != nil {
			return nil, err
		}
		if err = saveJSON(j.Validation.Destination, filepath.Join(j.Validation.Destination, ownerFile), ownerID(root)); err != nil {
			return nil, err
		}
		j.DestinationID, err = directoryIdentity(j.Validation.Destination)
		if err != nil {
			return nil, err
		}
		j.Originals, err = connectionConfigs(root)
		if err != nil {
			return nil, err
		}
		if err = checkpoint("switching"); err != nil {
			return nil, err
		}
		for path, raw := range j.Originals {
			var cfg map[string]json.RawMessage
			if err = json.Unmarshal(raw, &cfg); err != nil {
				return nil, err
			}
			var caching map[string]json.RawMessage
			if err = json.Unmarshal(cfg["caching"], &caching); err != nil || caching == nil {
				return nil, fmt.Errorf("managed connection has no cache settings")
			}
			target := filepath.Join(j.Validation.Destination, "repositories", filepath.Base(CacheDir(root, path)))
			caching["cacheDirectory"], _ = json.Marshal(target)
			cfg["caching"], _ = json.Marshal(caching)
			if err = saveJSON(root, path, cfg); err != nil {
				return nil, err
			}
		}
		if err = saveJSON(root, managementPath(root, "active"), ActiveRoot{Path: j.Validation.Destination, Generation: active.Generation + 1, ParentIdentity: j.Validation.ParentID}); err != nil {
			return nil, err
		}
	}
	if err = checkpoint("verifying"); err != nil {
		return nil, err
	}
	id, e := directoryIdentity(j.Validation.Destination)
	if e != nil || id != j.DestinationID {
		return nil, fmt.Errorf("destination identity changed after switch")
	}
	for path := range j.Originals {
		if verify != nil {
			if err = verify(path); err != nil {
				return nil, fmt.Errorf("new cache verification failed: %w", err)
			}
		}
	}
	// Deletion is permitted only after verified cutover. Retrying this stage never
	// switches back into a possibly partially deleted old cache.
	j.Verified = true
	if err = checkpoint("cleaning_old"); err != nil {
		return nil, err
	}
	if err = removeOldCache(ctx, root, j.Validation.Source.Path, j.Validation.SourceID); err != nil {
		return nil, err
	}
	j.Error = ""
	j.Originals = nil
	if err = checkpoint("completed"); err != nil {
		return nil, err
	}
	return j.Public(), nil
}
func removeOldCache(ctx context.Context, root, old, identity string) error {
	a, err := ReadActiveRoot(root)
	if err != nil {
		return err
	}
	if overlap(a.Path, old) {
		return fmt.Errorf("old cache overlaps active root")
	}
	if _, err := os.Lstat(old); os.IsNotExist(err) {
		return nil
	}
	id, err := directoryIdentity(old)
	if err != nil || id != identity {
		return fmt.Errorf("old cache filesystem identity changed; cleanup refused")
	}
	if err := plainPath(filepath.VolumeName(old)+string(filepath.Separator), old); err != nil {
		return err
	}
	if !samePath(old, vfs.AgentCacheDir(root)) {
		if ownershipErr := validateOwnedOrAbsent(root, old); ownershipErr != nil {
			// A failed final directory removal may already have removed the
			// ownership marker. The private journal's source identity was checked
			// above; permit retry only for that same, now-empty retired directory.
			directory, err := os.Open(old)
			if err != nil {
				return ownershipErr
			}
			names, readErr := directory.Readdirnames(1)
			closeErr := directory.Close()
			if len(names) != 0 || readErr != io.EOF {
				return ownershipErr
			}
			if closeErr != nil {
				return closeErr
			}
			currentID, err := directoryIdentity(old)
			if err != nil || currentID != identity {
				return fmt.Errorf("old cache identity changed before empty-directory cleanup")
			}
			if err := removeEmptyCacheDirectory(old); err != nil && !os.IsNotExist(err) {
				return err
			}
			return nil
		}
	}
	trusted := map[string]bool{}
	configs, err := connectionConfigs(root)
	if err != nil {
		return err
	}
	for path := range configs {
		trusted[filepath.Join("repositories", filepath.Base(CacheDir(root, path)))] = true
	}
	snapshots := map[string]os.FileInfo{}
	var files, dirs []string
	count := 0
	err = filepath.WalkDir(old, func(path string, d fs.DirEntry, e error) error {
		if e != nil {
			return e
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		count++
		if count > inventoryLimit {
			return fmt.Errorf("cache cleanup inventory bound exceeded")
		}
		snapshotInfo, infoErr := d.Info()
		if infoErr != nil {
			return infoErr
		}
		snapshots[path] = snapshotInfo
		if path == old {
			dirs = append(dirs, path)
			return nil
		}
		currentID, idErr := fileIdentity(path)
		if idErr != nil {
			return idErr
		}
		if strings.Split(currentID, ":")[0] != strings.Split(identity, ":")[0] {
			return fmt.Errorf("cache cleanup crosses a filesystem boundary")
		}
		rel, _ := filepath.Rel(old, path)
		if d.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("unclassified cache link retained")
		}
		if d.IsDir() {
			if !knownCacheDirectory(rel) {
				return fmt.Errorf("unclassified cache directory retained")
			}
			dirs = append(dirs, path)
			return nil
		}
		if !d.Type().IsRegular() || rel != ownerFile && !recognizedCacheFile(old, rel, trusted) {
			return fmt.Errorf("unclassified non-cache content retained")
		}
		files = append(files, path)
		return nil
	})
	if err != nil {
		return err
	}
	// Root-anchored removal prevents a concurrently replaced parent symlink
	// from redirecting deletion outside this inventoried cache tree.
	anchored, err := os.OpenRoot(old)
	if err != nil {
		return err
	}
	defer anchored.Close()
	opened, err := anchored.Open(".")
	if err != nil {
		return err
	}
	openedInfo, err := opened.Stat()
	_ = opened.Close()
	if err != nil || !os.SameFile(snapshots[old], openedInfo) {
		return fmt.Errorf("old cache root changed while opening")
	}
	verifyEntry := func(path string) error {
		for candidate := path; candidate != old; candidate = filepath.Dir(candidate) {
			rel, err := filepath.Rel(old, candidate)
			if err != nil {
				return err
			}
			current, err := anchored.Lstat(rel)
			if err != nil {
				return err
			}
			if current.Mode()&os.ModeSymlink != 0 || snapshots[candidate] == nil || !os.SameFile(current, snapshots[candidate]) {
				return fmt.Errorf("cache entry changed during cleanup")
			}
		}
		return nil
	}
	for _, path := range files {
		if err := ctx.Err(); err != nil {
			return err
		}
		if err := verifyEntry(path); err != nil {
			return err
		}
		rel, _ := filepath.Rel(old, path)
		if err := anchored.Remove(rel); err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	for i := len(dirs) - 1; i >= 0; i-- {
		if err := ctx.Err(); err != nil {
			return err
		}
		if dirs[i] == old {
			// Windows OpenRoot holds a directory handle without delete sharing.
			// Release our own handle after all anchored child removals, then
			// recheck identity and remove only an empty directory, never a file.
			if err := anchored.Close(); err != nil {
				return err
			}
			current, err := os.Lstat(old)
			if err != nil {
				return err
			}
			if !current.IsDir() || !os.SameFile(current, openedInfo) {
				return fmt.Errorf("old cache path changed before final directory removal")
			}
			if err := removeEmptyCacheDirectory(old); err != nil && !os.IsNotExist(err) {
				return err
			}
			continue
		}
		if err := verifyEntry(dirs[i]); err != nil {
			return err
		}
		rel, _ := filepath.Rel(old, dirs[i])
		if err := anchored.Remove(rel); err != nil && !os.IsNotExist(err) {
			return err
		}
	}

	return nil
}

// RecoverRelocation runs before accepting any task after process restart.
func RecoverRelocation(ctx context.Context, root, logs string, verify func(string) error) error {
	var j Relocation
	err := loadJSON(root, managementPath(root, "relocation"), &j)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	if j.Stage == "completed" || j.Stage == "failed" || j.Stage == "switched_cleanup_pending" {
		return nil
	}
	_, err = Relocate(ctx, root, logs, j.TaskID, "", nil, verify)
	// A durable pre-switch failure or post-switch cleanup-pending state is visible
	// and safe for normal operations. A damaged journal/rollback is not.
	if err != nil {
		var current Relocation
		if loadJSON(root, managementPath(root, "relocation"), &current) == nil && (current.Stage == "failed" || current.Stage == "switched_cleanup_pending") {
			return nil
		}
	}
	return err
}
func RelocationStatus(root string) map[string]any {
	// Heartbeats read only the bounded public projection, never recovery copies
	// of secret-bearing connection configurations.
	data, err := readRegular(root, managementPath(root, "relocation-status"), 16384)
	if err != nil {
		return nil
	}
	var public map[string]any
	if json.Unmarshal(data, &public) != nil {
		return nil
	}
	return public
}
func saveRelocation(root string, j interface{ Public() map[string]any }) error {
	if err := saveJSON(root, managementPath(root, "relocation"), j); err != nil {
		return err
	}
	return saveJSON(root, managementPath(root, "relocation-status"), j.Public())
}

// CacheUsage reports logical bytes with a strict local traversal budget. Links
// are not followed, aliases/hardlinks are counted once, and partial != zero.
func CacheUsage(ctx context.Context, root string) map[string]any {
	path := ActiveCacheRoot(root)
	var total int64
	count := 0
	complete := true
	seen := map[string]bool{}
	deadline := time.Now().Add(500 * time.Millisecond)
	err := filepath.WalkDir(path, func(path string, d fs.DirEntry, e error) error {
		if e != nil {
			return e
		}
		count++
		if count > 100000 || time.Now().After(deadline) {
			return fmt.Errorf("cache usage scan budget exceeded")
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		if d.Type()&os.ModeSymlink != 0 {
			return nil
		}
		if !d.Type().IsRegular() {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		key, err := fileIdentity(path)
		if err != nil {
			return err
		}
		if !seen[key] {
			total += info.Size()
			seen[key] = true
		}
		return nil
	})
	if err != nil {
		complete = false
	}
	return map[string]any{"logical_bytes": total, "complete": complete, "sampled_at": time.Now().UTC(), "basis": "logical_file_bytes"}
}

// RetryCleanup never accepts another destination or repeats an unverified switch.
func RetryCleanup(ctx context.Context, root, logs, taskID, previousID string, progress func(map[string]any), verify func(string) error) (map[string]any, error) {
	if err := privateDir(root, managementDir(root)); err != nil {
		return nil, err
	}
	release, err := filelock.Acquire(ctx, filepath.Join(managementDir(root), "relocation.lock"), false)
	if err != nil {
		return nil, err
	}
	defer release()
	var j Relocation
	if err := loadJSON(root, managementPath(root, "relocation"), &j); err != nil {
		return nil, err
	}
	if j.TaskID != previousID || j.Stage != "switched_cleanup_pending" || !j.Verified {
		return j.Public(), fmt.Errorf("only verified switched cache cleanup may be retried")
	}
	j.TaskID = taskID
	j.Error = ""
	j.Stage = "verifying"
	if err := saveRelocation(root, j); err != nil {
		return nil, err
	}
	if progress != nil {
		progress(j.Public())
	}
	return executeRelocation(ctx, root, logs, &j, progress, verify)
}

func storeActive(root string, a ActiveRoot) error {
	if a.Generation == 0 {
		err := os.Remove(managementPath(root, "active"))
		if err != nil && !os.IsNotExist(err) {
			return err
		}
		return syncDir(managementDir(root))
	}
	return saveJSON(root, managementPath(root, "active"), a)
}
