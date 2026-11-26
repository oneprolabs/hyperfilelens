package kopialayout

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"
	"time"

	"hyperfilelens/agent/internal/platform/vfs"
)

func managementFixture(t *testing.T) (string, string) {
	t.Helper()
	base, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	root := filepath.Join(base, "agent")
	parent := filepath.Join(base, "destination")
	for _, dir := range []string{root, parent, vfs.AgentCacheDir(root), ConfigDir(root)} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	if err := saveJSON(root, StatePath(root), migrationState{Version: 1, Status: "completed", Entries: map[string]*migrationEntry{}}); err != nil {
		t.Fatal(err)
	}
	if err := ApplyPolicy(context.Background(), root, Policy{1, 1, 1, 10*GIB + 1}); err != nil {
		t.Fatal(err)
	}
	return root, parent
}

// These lexical checks do not require the example drives/directories to exist.
// Windows runs exercise native filepath semantics, including drive-root handling.
func TestManagementCriticalDestinationBounds(t *testing.T) {
	type pathCase struct {
		path     string
		critical bool
	}
	cases := []pathCase{
		{"/", true},
		{"/etc", true},
		{"/etc/cache", true},
		{"/usr/local/cache", true},
		{"/System/Library/cache", true},
		{"/tmp/cache", false},
		{"/var/cache/hyperfilelens", false},
		{"/etc-backup/cache", false},
	}
	if runtime.GOOS == "windows" {
		cases = []pathCase{
			{`E:\`, true},
			{`E:/`, true},
			{`C:\`, true},
			{`E:\cache`, false},
			{`E:/cache`, false},
			{`E:\hfl-bk\new2`, false},
			{`C:\ProgramData\HyperFileLens\Agent\cache`, false},
			{`C:\Windows`, true},
			{`c:/WINDOWS/System32`, true},
			{`E:\Windows\Temp`, true},
			{`C:\cache\..\Windows\Temp`, true},
			{`E:\Program Files\cache`, true},
			{`E:\WindowsData\cache`, false},
			{`E:\Program Files backup\cache`, false},
			{`\\server\share\`, true},
			{`\\server\share\cache`, false},
		}
	}
	for _, tt := range cases {
		t.Run(tt.path, func(t *testing.T) {
			if got := criticalCacheDestination(tt.path); got != tt.critical {
				t.Fatalf("criticalCacheDestination(%q) = %t; want %t", tt.path, got, tt.critical)
			}
		})
	}
}

func TestManagementThresholdsAndAccountSpace(t *testing.T) {
	for _, b := range []int64{2, 10, 20, 21, 30, 50} {
		p := Policy{1, 1, b * GIB, (b + 10) * GIB}
		if err := p.Validate(); err != nil {
			t.Fatal(err)
		}
	}
	for _, p := range []Policy{{1, 1, 0, 10 * GIB}, {1, 1, -1, 10*GIB - 1}, {1, 1, 10 * GIB, 15 * GIB}, {2, 1, 10 * GIB, 20 * GIB}} {
		if p.Validate() == nil {
			t.Fatalf("accepted invalid policy: %+v", p)
		}
	}
	root, _ := managementFixture(t)
	p, err := ReadPolicy(root)
	if err != nil {
		t.Fatal(err)
	}
	for _, tt := range []struct {
		free  uint64
		state string
	}{{0, "blocked"}, {1, "warning"}, {uint64(p.Warning - 1), "warning"}, {uint64(p.Warning), "normal"}, {uint64(p.Warning + 1), "normal"}} {
		checked := false
		c, err := CheckSpace(root, func(path string) (uint64, uint64, uint64, error) {
			checked = true
			if path != vfs.AgentCacheDir(root) {
				t.Fatalf("wrong volume: %s", path)
			}
			return 100, 0, tt.free, nil
		})
		if err != nil || !checked || c.State != tt.state {
			t.Fatalf("check %+v %v", c, err)
		}
	}
	c, err := CheckSpace(root, func(string) (uint64, uint64, uint64, error) { return 0, 0, 0, errors.New("query failed") })
	if err == nil || c.State != "check_error" {
		t.Fatalf("fabricated low space: %+v %v", c, err)
	}
	if err := ApplyPolicy(context.Background(), root, DefaultPolicy()); err == nil {
		t.Fatal("stale revision accepted")
	}
}
func TestManagementRelocationPreservesStateAndCacheIdentity(t *testing.T) {
	root, parent := managementFixture(t)
	config := filepath.Join(ConfigDir(root), "repo-1.config")
	oldCache := CacheDir(root, config)
	connection := map[string]any{
		"storage": map[string]any{
			"type":   "filesystem",
			"config": map[string]any{"path": filepath.Join(filepath.Dir(root), "real-repository")},
		},
		"caching":  map[string]any{"cacheDirectory": oldCache},
		"password": "must-remain-private",
	}
	if err := saveJSON(root, config, connection); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "identity"), []byte("identity"), 0o600); err != nil {
		t.Fatal(err)
	}
	v, err := ValidateDestination(context.Background(), root, parent, filepath.Join(root, "logs"))
	if err != nil {
		t.Fatal(err)
	}
	data, err := Relocate(context.Background(), root, filepath.Join(root, "logs"), "relocate-1", v.Token, nil, func(path string) error {
		if filepath.Base(CacheDir(root, path)) != filepath.Base(oldCache) {
			t.Fatal("warm namespace identity changed")
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if data["stage"] != "completed" || ActiveCacheRoot(root) != v.Destination {
		t.Fatalf("bad cutover: %+v", data)
	}
	raw, err := os.ReadFile(config)
	if err != nil {
		t.Fatal(err)
	}
	var relocated map[string]any
	if err := json.Unmarshal(raw, &relocated); err != nil {
		t.Fatalf("decode relocated connection: %v", err)
	}
	// Compare decoded values: JSON escapes Windows path separators. Only the
	// cache directory may change; credentials and storage must remain intact.
	connection["caching"] = map[string]any{
		"cacheDirectory": filepath.Join(v.Destination, "repositories", filepath.Base(oldCache)),
	}
	if !reflect.DeepEqual(relocated, connection) {
		t.Fatal("relocated connection changed protected fields or has the wrong cache directory")
	}
	if _, err := os.Stat(filepath.Join(root, "identity")); err != nil {
		t.Fatal("identity deleted")
	}
	if _, err := os.Stat(vfs.AgentCacheDir(root)); !os.IsNotExist(err) {
		t.Fatal("old root not removed")
	}
	if strings.Contains(toJSON(t, RelocationStatus(root)), "must-remain-private") {
		t.Fatal("journal secrets exposed")
	}
}
func TestManagementVerificationFailureRollsBackBeforeAdmission(t *testing.T) {
	root, parent := managementFixture(t)
	v, err := ValidateDestination(context.Background(), root, parent, filepath.Join(root, "logs"))
	if err != nil {
		t.Fatal(err)
	}
	config := filepath.Join(ConfigDir(root), "repo-1.config")
	raw := []byte(`{"storage":{"type":"s3"},"caching":{"cacheDirectory":"old"}}`)
	if err := writeDurable(root, config, raw); err != nil {
		t.Fatal(err)
	}
	data, err := Relocate(context.Background(), root, filepath.Join(root, "logs"), "verify-fail", v.Token, nil, func(string) error { return errors.New("verification failed") })
	if err == nil || data["stage"] != "failed" {
		t.Fatalf("bad failure: %+v %v", data, err)
	}
	if ActiveCacheRoot(root) != vfs.AgentCacheDir(root) {
		t.Fatal("failed verification left new root active")
	}
	got, _ := os.ReadFile(config)
	if string(got) != string(raw) {
		t.Fatal("connection was not rolled back")
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	release, err := AcquireUsage(ctx, root)
	if err != nil {
		t.Fatal(err)
	}
	release()
	// A failed verification leaves the owned destination reusable. The next
	// explicit task must work without credentials or manually deleting it.
	retry, err := ValidateDestination(context.Background(), root, parent, filepath.Join(root, "logs"))
	if err != nil {
		t.Fatal(err)
	}
	result, err := Relocate(context.Background(), root, filepath.Join(root, "logs"), "verified-retry", retry.Token, nil, func(path string) error {
		return VerifyCacheConnection(context.Background(), root, path)
	})
	if err != nil || result["stage"] != "completed" {
		t.Fatalf("explicit retry failed: %+v %v", result, err)
	}
}
func TestManagementCleanupClosesOldRootBeforeRemovingIt(t *testing.T) {
	root, parent := managementFixture(t)
	old := ActiveCacheRoot(root)
	logs := filepath.Join(root, "logs")
	validation, err := ValidateDestination(context.Background(), root, parent, logs)
	if err != nil {
		t.Fatal(err)
	}
	result, err := Relocate(context.Background(), root, logs, "remove-empty-old-root", validation.Token, nil, nil)
	if err != nil || result["stage"] != "completed" {
		t.Fatalf("old root cleanup failed: %+v %v", result, err)
	}
	if _, err := os.Lstat(old); !os.IsNotExist(err) {
		t.Fatalf("empty old cache root was not removed: %v", err)
	}
	if ActiveCacheRoot(root) != validation.Destination {
		t.Fatal("successful cleanup changed the active destination")
	}
}

func TestManagementEmptyDirectoryRemovalNeverDeletesReplacementContent(t *testing.T) {
	root := t.TempDir()
	file := filepath.Join(root, "business-file")
	if err := os.WriteFile(file, []byte("retain-business-content"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := removeEmptyCacheDirectory(file); err == nil {
		t.Fatal("directory-only cleanup deleted a regular file")
	}
	if data, err := os.ReadFile(file); err != nil || string(data) != "retain-business-content" {
		t.Fatal("business file was changed")
	}
	directory := filepath.Join(root, "directory")
	if err := os.Mkdir(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "business-file"), []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := removeEmptyCacheDirectory(directory); err == nil {
		t.Fatal("directory-only cleanup deleted non-empty replacement content")
	}
	long := root
	for len(long) < 300 {
		long = filepath.Join(long, strings.Repeat("a", 70))
	}
	if err := os.MkdirAll(long, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := removeEmptyCacheDirectory(long); err != nil {
		t.Fatalf("empty long-path directory was not removed: %v", err)
	}
}

func TestManagementCleanupRetryRetainsContentButAcceptsEmptyRetiredRoot(t *testing.T) {
	root, parent := managementFixture(t)
	logs := filepath.Join(root, "logs")
	validation, err := ValidateDestination(context.Background(), root, parent, logs)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Relocate(context.Background(), root, logs, "first-cache", validation.Token, nil, nil); err != nil {
		t.Fatal(err)
	}
	retired := ActiveCacheRoot(root)
	business := filepath.Join(retired, "business-file")
	if err := os.WriteFile(business, []byte("retain-business-content"), 0o600); err != nil {
		t.Fatal(err)
	}
	next := filepath.Join(filepath.Dir(parent), "next-destination")
	if err := os.Mkdir(next, 0o700); err != nil {
		t.Fatal(err)
	}
	validation, err = ValidateDestination(context.Background(), root, next, logs)
	if err != nil {
		t.Fatal(err)
	}
	result, err := Relocate(context.Background(), root, logs, "second-cache", validation.Token, nil, nil)
	if err == nil || result["stage"] != "switched_cleanup_pending" {
		t.Fatalf("unknown content was not retained: %+v %v", result, err)
	}
	// Reproduce a prior cleanup that removed its marker before root removal failed.
	if err := os.Remove(filepath.Join(retired, ownerFile)); err != nil {
		t.Fatal(err)
	}
	result, err = RetryCleanup(context.Background(), root, logs, "nonempty-retry", "second-cache", nil, nil)
	if err == nil || result["stage"] != "switched_cleanup_pending" {
		t.Fatal("a missing marker allowed non-empty old cache cleanup")
	}
	if data, err := os.ReadFile(business); err != nil || string(data) != "retain-business-content" {
		t.Fatal("retry touched unknown business data")
	}
	if err := os.Remove(business); err != nil { // Test fixture cleanup, not product behavior.
		t.Fatal(err)
	}
	result, err = RetryCleanup(context.Background(), root, logs, "empty-retry", "nonempty-retry", nil, nil)
	if err != nil || result["stage"] != "completed" {
		t.Fatalf("empty verified retired root could not be cleaned: %+v %v", result, err)
	}
	if _, err := os.Lstat(retired); !os.IsNotExist(err) {
		t.Fatalf("retired root remains: %v", err)
	}
	if ActiveCacheRoot(root) != validation.Destination {
		t.Fatal("cleanup retry changed the active destination")
	}
}

func TestManagementCleanupFailureRetainsNewRootAndRetries(t *testing.T) {
	root, parent := managementFixture(t)
	unknown := filepath.Join(vfs.AgentCacheDir(root), "business-file")
	if err := os.WriteFile(unknown, []byte("unique"), 0o600); err != nil {
		t.Fatal(err)
	}
	v, err := ValidateDestination(context.Background(), root, parent, filepath.Join(root, "logs"))
	if err != nil {
		t.Fatal(err)
	}
	result, err := Relocate(context.Background(), root, filepath.Join(root, "logs"), "cleanup-fail", v.Token, nil, nil)
	if err == nil || result["stage"] != "switched_cleanup_pending" {
		t.Fatalf("bad partial outcome: %+v %v", result, err)
	}
	if ActiveCacheRoot(root) != v.Destination {
		t.Fatal("partial cleanup rolled back")
	}
	raw, _ := os.ReadFile(unknown)
	if string(raw) != "unique" {
		t.Fatal("business data touched")
	}
	// User explicitly removes the test fixture's unknown data; the product never does.
	if err := os.Remove(unknown); err != nil {
		t.Fatal(err)
	}
	result, err = RetryCleanup(context.Background(), root, filepath.Join(root, "logs"), "retry", "cleanup-fail", nil, nil)
	if err != nil || result["stage"] != "completed" {
		t.Fatalf("retry %+v %v", result, err)
	}
}
func TestManagementDrainRejectsNewReadersWithoutKillingExisting(t *testing.T) {
	root, parent := managementFixture(t)
	old, err := AcquireUsage(context.Background(), root)
	if err != nil {
		t.Fatal(err)
	}
	defer old()
	v, err := ValidateDestination(context.Background(), root, parent, filepath.Join(root, "logs"))
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		_, err := Relocate(ctx, root, filepath.Join(root, "logs"), "drain", v.Token, nil, nil)
		done <- err
	}()
	deadline := time.Now().Add(time.Second)
	for {
		if state := RelocationStatus(root); state != nil && state["stage"] == "waiting_for_users" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("drain not persisted")
		}
		time.Sleep(time.Millisecond)
	}
	readerCtx, stop := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer stop()
	if release, err := AcquireUsage(readerCtx, root); err == nil {
		release()
		t.Fatal("new reader admitted during drain")
	}
	old()
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}
func TestManagementRejectsUnsafeOrUnownedDestination(t *testing.T) {
	root, parent := managementFixture(t)
	for _, path := range []string{"relative", root, vfs.AgentCacheDir(root), "/etc"} {
		if _, err := ValidateDestination(context.Background(), root, path, filepath.Join(root, "logs")); err == nil {
			t.Fatalf("unsafe destination accepted: %s", path)
		}
	}
	owned := filepath.Join(parent, ".hyperfilelens-cache-"+ownerID(root))
	_ = os.MkdirAll(owned, 0o700)
	_ = os.WriteFile(filepath.Join(owned, "user-file"), []byte("business"), 0o600)
	if _, err := ValidateDestination(context.Background(), root, parent, filepath.Join(root, "logs")); err == nil {
		t.Fatal("unowned directory accepted")
	}
}

func toJSON(t *testing.T, v any) string {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func TestManagementAdmittedNestedUsersCanDrain(t *testing.T) {
	root, parent := managementFixture(t)
	ctx, release, err := AcquireUsageContext(context.Background(), root)
	if err != nil {
		t.Fatal(err)
	}
	defer release()
	v, err := ValidateDestination(ctx, root, parent, filepath.Join(root, "logs"))
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() {
		_, err := Relocate(context.Background(), root, filepath.Join(root, "logs"), "nested", v.Token, nil, nil)
		done <- err
	}()
	deadline := time.Now().Add(time.Second)
	for {
		if state := RelocationStatus(root); state != nil && state["stage"] == "waiting_for_users" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("drain did not begin")
		}
		time.Sleep(time.Millisecond)
	}
	child, err := AcquireUsage(ctx, root)
	if err != nil {
		t.Fatal(err)
	}
	release()
	select {
	case <-done:
		t.Fatal("detached child lost its lease")
	case <-time.After(20 * time.Millisecond):
	}
	child()
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}

func TestManagementRecoveryRestoresPartialReferencesBeforeRetry(t *testing.T) {
	root, parent := managementFixture(t)
	config := filepath.Join(ConfigDir(root), "repo-1.config")
	original := []byte(`{"storage":{"type":"s3"},"caching":{"cacheDirectory":"original-cache"}}`)
	if err := writeDurable(root, config, original); err != nil {
		t.Fatal(err)
	}
	v, err := ValidateDestination(context.Background(), root, parent, filepath.Join(root, "logs"))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(v.Destination, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := saveJSON(v.Destination, filepath.Join(v.Destination, ownerFile), ownerID(root)); err != nil {
		t.Fatal(err)
	}
	id, _ := directoryIdentity(v.Destination)
	j := Relocation{TaskID: "interrupted-switch", Stage: "switching", Validation: v, DestinationID: id, Originals: map[string]json.RawMessage{config: original}}
	if err := saveJSON(root, managementPath(root, "relocation"), j); err != nil {
		t.Fatal(err)
	}
	if err := writeDurable(root, config, []byte(`{"storage":{"type":"s3"},"caching":{"cacheDirectory":"partially-switched"}}`)); err != nil {
		t.Fatal(err)
	}
	if err := RecoverRelocation(context.Background(), root, filepath.Join(root, "logs"), func(string) error { return errors.New("verification fails after restart") }); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(config)
	if err != nil || string(raw) != string(original) {
		t.Fatalf("partial references replaced recovery original: %s %v", raw, err)
	}
	if ActiveCacheRoot(root) != vfs.AgentCacheDir(root) {
		t.Fatal("restart failure did not retain old active root")
	}
}

func TestManagementResumesInterruptedEmptyDestinationPreparation(t *testing.T) {
	root, parent := managementFixture(t)
	v, err := ValidateDestination(context.Background(), root, parent, filepath.Join(root, "logs"))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(v.Destination, 0o700); err != nil {
		t.Fatal(err)
	}
	j := Relocation{TaskID: "interrupted-prepare", Stage: "preparing", Validation: v}
	if err := saveRelocation(root, j); err != nil {
		t.Fatal(err)
	}
	if err := RecoverRelocation(context.Background(), root, filepath.Join(root, "logs"), nil); err != nil {
		t.Fatal(err)
	}
	if state := RelocationStatus(root); state["stage"] != "completed" {
		t.Fatalf("empty prepared destination not resumed: %+v", state)
	}
}

func TestManagementSpaceCheckIdentityAndUnknownJSON(t *testing.T) {
	root, _ := managementFixture(t)
	check, err := CheckSpace(root, func(string) (uint64, uint64, uint64, error) { return 0, 0, 0, errors.New("unavailable") })
	if err == nil || check.CheckID == "" {
		t.Fatal("failed checks require stable occurrence identity")
	}
	raw, err := json.Marshal(check)
	if err != nil {
		t.Fatal(err)
	}
	var value map[string]any
	if err := json.Unmarshal(raw, &value); err != nil {
		t.Fatal(err)
	}
	if value["available_bytes"] != nil {
		t.Fatalf("unknown space became a number: %s", raw)
	}
	known, err := CheckSpace(root, func(string) (uint64, uint64, uint64, error) { return 10, 10, 0, nil })
	if err != nil || known.CheckID == check.CheckID || known.FilesystemID == "" {
		t.Fatal("new checks must identify their occurrence and cache volume")
	}
	raw, _ = json.Marshal(known)
	_ = json.Unmarshal(raw, &value)
	if value["available_bytes"] != float64(0) {
		t.Fatalf("confirmed zero is not unknown: %s", raw)
	}
}

func TestBinaryDefaultsAndRetainedDecimalPolicyBytes(t *testing.T) {
	defaults := DefaultPolicy()
	if defaults.Blocking != 10_737_418_240 || defaults.Warning != 21_474_836_480 {
		t.Fatalf("defaults are not GiB: %+v", defaults)
	}
	root, _ := managementFixture(t)
	legacy := Policy{1, 4, 10_000_000_000, 20_000_000_000}
	path := managementPath(root, "policy")
	if err := saveJSON(root, path, legacy); err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	restored, err := ReadPolicy(root)
	if err != nil || restored != legacy {
		t.Fatalf("legacy changed: %+v %v", restored, err)
	}
	after, err := os.ReadFile(path)
	if err != nil || string(before) != string(after) {
		t.Fatal("reading rewrote the policy")
	}
	next := Policy{1, 5, 12 * GIB, 22 * GIB}
	if err := ApplyPolicy(context.Background(), root, next); err != nil {
		t.Fatal(err)
	}
	restored, err = ReadPolicy(root)
	if err != nil || restored != next {
		t.Fatalf("new policy: %+v %v", restored, err)
	}
	if err := ApplyPolicy(context.Background(), root, legacy); err == nil {
		t.Fatal("accepted stale decimal policy")
	}
}

func TestImplicitBinaryDefaultDoesNotRejectRetainedRevisionZero(t *testing.T) {
	root, _ := managementFixture(t)
	if err := os.Remove(managementPath(root, "policy")); err != nil {
		t.Fatal(err)
	}
	legacy := Policy{1, 0, 10_000_000_000, 20_000_000_000}
	if err := ApplyPolicy(context.Background(), root, legacy); err != nil {
		t.Fatal(err)
	}
	restored, err := ReadPolicy(root)
	if err != nil || restored != legacy {
		t.Fatalf("lost implicit old policy: %+v %v", restored, err)
	}
	if err := ApplyPolicy(context.Background(), root, DefaultPolicy()); err == nil {
		t.Fatal("a retained revision zero was silently replaced by new defaults")
	}
}

func TestManagementRoundTripToCanonicalDefaultCache(t *testing.T) {
	for _, useCachePath := range []bool{false, true} {
		t.Run(fmt.Sprintf("cache-path-%t", useCachePath), func(t *testing.T) {
			root, parent := managementFixture(t)
			config := filepath.Join(ConfigDir(root), "repo-1.config")
			raw, _ := json.Marshal(map[string]any{"storage": map[string]any{"type": "filesystem", "config": map[string]any{"path": filepath.Join(filepath.Dir(root), "real-backup-repository")}}, "caching": map[string]any{"cacheDirectory": CacheDir(root, config)}})
			if err := writeDurable(root, config, raw); err != nil {
				t.Fatal(err)
			}
			logs := filepath.Join(root, "logs")
			verify := func(path string) error { return VerifyCacheConnection(context.Background(), root, path) }
			first, err := ValidateDestination(context.Background(), root, parent, logs)
			if err != nil {
				t.Fatal(err)
			}
			if _, err = Relocate(context.Background(), root, logs, "move-out", first.Token, nil, verify); err != nil {
				t.Fatal(err)
			}
			requested := root
			if useCachePath {
				requested = vfs.AgentCacheDir(root)
			}
			back, err := ValidateDestination(context.Background(), root, requested, logs)
			if err != nil {
				t.Fatal(err)
			}
			if back.Destination != vfs.AgentCacheDir(root) {
				t.Fatalf("not canonical: %s", back.Destination)
			}
			if _, err = Relocate(context.Background(), root, logs, "move-back", back.Token, nil, verify); err != nil {
				t.Fatal(err)
			}
			if ActiveCacheRoot(root) != vfs.AgentCacheDir(root) {
				t.Fatal("default cache not activated")
			}
			if _, err := os.Stat(config); err != nil {
				t.Fatal("persistent connection removed")
			}
			if _, err := os.Stat(first.Destination); !os.IsNotExist(err) {
				t.Fatal("old external cache not cleaned")
			}
			for _, bad := range []string{filepath.Join(root, "data"), filepath.Join(root, "runtime"), logs, filepath.Join(root, "bin")} {
				if err := os.MkdirAll(bad, 0o700); err != nil {
					t.Fatal(err)
				}
				if _, err := ValidateDestination(context.Background(), root, bad, logs); err == nil {
					t.Fatalf("protected destination accepted: %s", bad)
				}
			}
		})
	}
}

func TestManagementDefaultRestoreAcceptsEmptyRepositoriesScaffold(t *testing.T) {
	for _, useCachePath := range []bool{false, true} {
		t.Run(fmt.Sprintf("cache-path-%t", useCachePath), func(t *testing.T) {
			root, parent := managementFixture(t)
			logs := filepath.Join(root, "logs")
			out, err := ValidateDestination(context.Background(), root, parent, logs)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := Relocate(context.Background(), root, logs, "move-out", out.Token, nil, nil); err != nil {
				t.Fatal(err)
			}
			if err := os.MkdirAll(LegacyConfigDir(root), 0o700); err != nil {
				t.Fatal(err)
			}
			requested := root
			if useCachePath {
				requested = vfs.AgentCacheDir(root)
			}
			back, err := ValidateDestination(context.Background(), root, requested, logs)
			if err != nil {
				t.Fatalf("empty default repositories scaffold rejected: %v", err)
			}
			if back.Destination != vfs.AgentCacheDir(root) {
				t.Fatalf("restoration did not select canonical cache: %s", back.Destination)
			}
			if _, err := os.Lstat(filepath.Join(back.Destination, ownerFile)); !os.IsNotExist(err) {
				t.Fatal("validation wrote an ownership marker")
			}
			if err := validateOwnedOrAbsent(root, back.Destination); err == nil {
				t.Fatal("default restoration loosened general cache ownership checks")
			}
			result, err := Relocate(context.Background(), root, logs, "restore-default", back.Token, nil, nil)
			if err != nil || result["stage"] != "completed" {
				t.Fatalf("default restoration failed: %+v %v", result, err)
			}
			if ActiveCacheRoot(root) != back.Destination {
				t.Fatal("default cache was not activated")
			}
			var owner string
			if err := loadJSON(back.Destination, filepath.Join(back.Destination, ownerFile), &owner); err != nil || owner != ownerID(root) {
				t.Fatal("verified restoration did not initialize its owned cache")
			}
		})
	}
}

func TestManagementDefaultRestoreScaffoldNeverAdoptsUnexpectedContent(t *testing.T) {
	for _, name := range []string{"business-file", "extra-empty-directory", "nested-empty-directory", "legacy-config", "foreign-owner", "invalid-owner"} {
		t.Run(name, func(t *testing.T) {
			root, parent := managementFixture(t)
			logs := filepath.Join(root, "logs")
			out, err := ValidateDestination(context.Background(), root, parent, logs)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := Relocate(context.Background(), root, logs, "move-out", out.Token, nil, nil); err != nil {
				t.Fatal(err)
			}
			if err := os.MkdirAll(LegacyConfigDir(root), 0o700); err != nil {
				t.Fatal(err)
			}
			unexpected := filepath.Join(LegacyConfigDir(root), "business-file")
			data := []byte("retain-business-data")
			switch name {
			case "extra-empty-directory":
				unexpected = filepath.Join(vfs.AgentCacheDir(root), "documents")
			case "nested-empty-directory":
				unexpected = filepath.Join(LegacyConfigDir(root), "unknown-directory")
			case "legacy-config":
				unexpected = filepath.Join(LegacyConfigDir(root), "repo-1.config")
			case "foreign-owner":
				unexpected = filepath.Join(vfs.AgentCacheDir(root), ownerFile)
				data = []byte(`"another-installation"`)
			case "invalid-owner":
				unexpected = filepath.Join(vfs.AgentCacheDir(root), ownerFile)
				data = []byte("not-json")
			}
			if strings.Contains(name, "directory") {
				if err := os.Mkdir(unexpected, 0o700); err != nil {
					t.Fatal(err)
				}
			} else if err := os.WriteFile(unexpected, data, 0o600); err != nil {
				t.Fatal(err)
			}
			if _, err := ValidateDestination(context.Background(), root, root, logs); err == nil {
				t.Fatal("unexpected default-cache content was adopted")
			}
			if ActiveCacheRoot(root) != out.Destination {
				t.Fatal("failed validation changed the active cache")
			}
			if strings.Contains(name, "directory") {
				if info, err := os.Lstat(unexpected); err != nil || !info.IsDir() {
					t.Fatal("unexpected directory was changed")
				}
			} else if got, err := os.ReadFile(unexpected); err != nil || string(got) != string(data) {
				t.Fatal("unexpected data or ownership marker was changed")
			}
		})
	}
}

func TestManagementDefaultRestoreRejectsLinkedRepositoriesScaffold(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink fixture requires privileged Windows setup")
	}
	root, parent := managementFixture(t)
	logs := filepath.Join(root, "logs")
	out, err := ValidateDestination(context.Background(), root, parent, logs)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Relocate(context.Background(), root, logs, "move-out", out.Token, nil, nil); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(vfs.AgentCacheDir(root), 0o700); err != nil {
		t.Fatal(err)
	}
	outside := t.TempDir() // Even an empty directory elsewhere cannot be adopted through a link.
	if err := os.Symlink(outside, LegacyConfigDir(root)); err != nil {
		t.Fatal(err)
	}
	if _, err := ValidateDestination(context.Background(), root, root, logs); err == nil {
		t.Fatal("linked repositories scaffold was adopted")
	}
	if info, err := os.Lstat(LegacyConfigDir(root)); err != nil || info.Mode()&os.ModeSymlink == 0 {
		t.Fatal("linked scaffold was changed")
	}
	if entries, err := os.ReadDir(outside); err != nil || len(entries) != 0 {
		t.Fatal("external directory was changed")
	}
}

func TestManagementDefaultRestoreRechecksScaffoldBeforeSwitch(t *testing.T) {
	root, parent := managementFixture(t)
	logs := filepath.Join(root, "logs")
	out, err := ValidateDestination(context.Background(), root, parent, logs)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Relocate(context.Background(), root, logs, "move-out", out.Token, nil, nil); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(LegacyConfigDir(root), 0o700); err != nil {
		t.Fatal(err)
	}
	back, err := ValidateDestination(context.Background(), root, root, logs)
	if err != nil {
		t.Fatal(err)
	}
	business := filepath.Join(LegacyConfigDir(root), "business-file")
	if err := os.WriteFile(business, []byte("preserve"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Relocate(context.Background(), root, logs, "restore-default", back.Token, nil, nil); err == nil {
		t.Fatal("new content inserted after validation was adopted")
	}
	if got, err := os.ReadFile(business); err != nil || string(got) != "preserve" {
		t.Fatal("new content was changed")
	}
	if ActiveCacheRoot(root) != out.Destination {
		t.Fatal("revalidation failure changed the active cache")
	}
}

func TestManagementExternalDestinationCannotAdoptEmptyRepositoriesScaffold(t *testing.T) {
	root, parent := managementFixture(t)
	unowned := filepath.Join(parent, ".hyperfilelens-cache-"+ownerID(root))
	if err := os.MkdirAll(filepath.Join(unowned, "repositories"), 0o700); err != nil {
		t.Fatal(err)
	}
	if _, err := ValidateDestination(context.Background(), root, parent, filepath.Join(root, "logs")); err == nil {
		t.Fatal("ordinary destination adopted a non-empty unowned scaffold")
	}
	if _, err := os.Lstat(filepath.Join(unowned, ownerFile)); !os.IsNotExist(err) {
		t.Fatal("failed validation wrote an ownership marker")
	}
}

func TestManagementDefaultRestoreDoesNotAdoptUnknownFilesOrOverrideOtherProtection(t *testing.T) {
	root, parent := managementFixture(t)
	logs := filepath.Join(root, "logs")
	out, err := ValidateDestination(context.Background(), root, parent, logs)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Relocate(context.Background(), root, logs, "out", out.Token, nil, nil); err != nil {
		t.Fatal(err)
	}
	canonical := vfs.AgentCacheDir(root)
	if err := os.MkdirAll(canonical, 0o700); err != nil {
		t.Fatal(err)
	}
	unknown := filepath.Join(canonical, "business-data")
	if err := os.WriteFile(unknown, []byte("preserve"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := ValidateDestination(context.Background(), root, root, logs); err == nil {
		t.Fatal("non-owned data adopted")
	}
	got, err := os.ReadFile(unknown)
	if err != nil || string(got) != "preserve" {
		t.Fatal("unknown data changed")
	}
	if _, err := ValidateDestination(context.Background(), root, root, root); err == nil {
		t.Fatal("overlapping custom log root bypassed")
	}
	config := filepath.Join(ConfigDir(root), "repo-1.config")
	raw, _ := json.Marshal(map[string]any{"storage": map[string]any{"type": "filesystem", "config": map[string]any{"path": canonical}}})
	if err := writeDurable(root, config, raw); err != nil {
		t.Fatal(err)
	}
	if _, err := ValidateDestination(context.Background(), root, root, logs); err == nil || !strings.Contains(err.Error(), "protected") {
		t.Fatalf("real repository protection bypassed: %v", err)
	}
}

func TestManagementDefaultRestoreRejectsSymlinkWithoutTouchingExternalData(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink fixture requires privileged Windows setup")
	}
	root, parent := managementFixture(t)
	logs := filepath.Join(root, "logs")
	out, err := ValidateDestination(context.Background(), root, parent, logs)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Relocate(context.Background(), root, logs, "out", out.Token, nil, nil); err != nil {
		t.Fatal(err)
	}
	outside := t.TempDir()
	sentinel := filepath.Join(outside, "business-data")
	if err := os.WriteFile(sentinel, []byte("preserve"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, vfs.AgentCacheDir(root)); err != nil {
		t.Fatal(err)
	}
	if _, err := ValidateDestination(context.Background(), root, root, logs); err == nil {
		t.Fatal("linked default cache accepted")
	}
	entries, err := os.ReadDir(outside)
	if err != nil || len(entries) != 1 {
		t.Fatal("default restoration touched external data")
	}
	data, err := os.ReadFile(sentinel)
	if err != nil || string(data) != "preserve" {
		t.Fatal("external file changed")
	}
}

func TestManagementDefaultRestoreStillEnforcesAvailableSpace(t *testing.T) {
	root, parent := managementFixture(t)
	logs := filepath.Join(root, "logs")
	out, err := ValidateDestination(context.Background(), root, parent, logs)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Relocate(context.Background(), root, logs, "out", out.Token, nil, nil); err != nil {
		t.Fatal(err)
	}
	policy := Policy{1, 2, math.MaxInt64 - 10*GIB, math.MaxInt64}
	if err := ApplyPolicy(context.Background(), root, policy); err != nil {
		t.Fatal(err)
	}
	if _, err := ValidateDestination(context.Background(), root, root, logs); err == nil || !strings.Contains(err.Error(), "below blocking threshold") {
		t.Fatalf("space guard bypassed: %v", err)
	}
	after, err := ReadPolicy(root)
	if err != nil || after != policy {
		t.Fatal("validation silently lowered thresholds")
	}
}
