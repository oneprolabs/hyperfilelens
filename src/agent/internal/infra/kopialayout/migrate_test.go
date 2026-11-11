package kopialayout

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func put(t *testing.T, path string, data []byte) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
}

func testMigrator(t *testing.T, root string) *migrator {
	t.Helper()
	return &migrator{
		root: root, logs: filepath.Join(root, "logs"),
		checkUsers: func(context.Context, string, bool) error { return nil },
	}
}

func seedConnection(t *testing.T, root, name string) []byte {
	t.Helper()
	config := map[string]any{
		"storage": map[string]any{"type": "s3", "config": map[string]any{"bucket": "isolated-test"}},
		"caching": map[string]any{
			"cacheDirectory": filepath.Base(CacheDir(root, filepath.Join(LegacyConfigDir(root), name))),
			"maxCacheSize":   123456789, "maxMetadataCacheSize": 23456789,
		},
		"hostname": "existing-host", "username": "existing-user",
		"futureField": map[string]any{"preserved": true},
	}
	data, err := json.Marshal(config)
	if err != nil {
		t.Fatal(err)
	}
	put(t, filepath.Join(LegacyConfigDir(root), name), data)
	return data
}

func TestMigrationKeepsFilenamesWarmCacheAndUnknownConfigFields(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(LegacyConfigDir(root), "repo-42.config")
	old := seedConnection(t, root, "repo-42.config")
	maintenance := seedConnection(t, root, "repo-42.maintenance.config")
	put(t, source+".connection.sha256", []byte(strings.Repeat("a", 64)+"\n"))
	put(t, source+".lock", nil)
	cache := CacheDir(root, source)
	put(t, filepath.Join(cache, "CACHEDIR.TAG"), []byte(cacheMarker+"\n"))
	put(t, filepath.Join(cache, "contents", "ab", "example.f"), []byte("warm-cache"))
	logSource := filepath.Join(cache, "kopia", "cli-logs", "kopia-20261109-000000-123-snapshot-create.0.log")
	put(t, logSource, []byte("historical diagnostic"))
	m := testMigrator(t, root)
	switched := false
	m.switchReferences = func(_ context.Context, paths map[string]string) error {
		if paths[source] != filepath.Join(ConfigDir(root), "repo-42.config") {
			t.Fatalf("incorrect alias mapping")
		}
		if _, err := os.Stat(source); err != nil {
			t.Fatalf("original removed before references switched: %v", err)
		}
		switched = true
		return nil
	}
	if err := m.run(context.Background()); err != nil {
		t.Fatal(err)
	}
	if !switched || m.state.Status != "completed" {
		t.Fatalf("migration did not commit")
	}
	target := ResolveConfig(root, source)
	if target != filepath.Join(root, "data", "kopia", "repositories", "repo-42.config") {
		t.Fatal("configuration was not grouped under the Kopia module")
	}
	if CacheDir(root, target) != cache {
		t.Fatal("moving config orphaned the warm cache")
	}
	data, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	want, _ := relocatedConfig(root, source, old)
	if !bytes.Equal(data, want) {
		t.Fatal("connection fields or cache budgets changed")
	}
	maint, err := os.ReadFile(filepath.Join(ConfigDir(root), "repo-42.maintenance.config"))
	wantMaint, _ := relocatedConfig(root, filepath.Join(LegacyConfigDir(root), "repo-42.maintenance.config"), maintenance)
	if err != nil || !bytes.Equal(maint, wantMaint) {
		t.Fatal("maintenance config was not preserved")
	}
	for _, path := range []string{source, source + ".connection.sha256", source + ".lock", logSource} {
		if _, err := os.Lstat(path); !os.IsNotExist(err) {
			t.Fatalf("old non-cache file remains: %s", filepath.Base(path))
		}
	}
	if _, err := os.Stat(LockPath(root, target)); err != nil {
		t.Fatalf("runtime lock was not prepared: %v", err)
	}
	if _, err := os.Stat(filepath.Join(cache, "contents", "ab", "example.f")); err != nil {
		t.Fatal("warm cache was removed")
	}
	if runtime.GOOS != "windows" {
		info, _ := os.Stat(target)
		if info.Mode().Perm() != 0o600 {
			t.Fatal("connection permissions are not private")
		}
	}
	// Completed entries must not pin active configs to their migration-time hash.
	put(t, target, append(data, '\n'))
	if err := testMigrator(t, root).run(context.Background()); err != nil {
		t.Fatalf("repeated upgrade failed: %v", err)
	}
}

func TestInventoryRecognizesHistoricalKopiaMetadataWithoutCacheMarker(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(LegacyConfigDir(root), "repo-29.config")
	seedConnection(t, root, filepath.Base(source))
	cache := CacheDir(root, source)
	put(t, filepath.Join(cache, "kopia.repository"), []byte(`{"format":"kopia"}`))
	put(t, filepath.Join(cache, "kopia.blobcfg"), []byte("blob-config"))
	// This is a stale lock left by an older Agent after its config was removed.
	put(t, filepath.Join(LegacyConfigDir(root), "repo-35-nas-9964eb569383e921.config.lock"), nil)

	m := testMigrator(t, root)
	if err := m.run(context.Background()); err != nil {
		t.Fatalf("historical Kopia metadata was retained: %v (retained=%v)", err, m.state.Retained)
	}
	if m.state.Status != "completed" || len(m.state.Retained) != 0 {
		t.Fatalf("expected disposable cache, status=%s retained=%v", m.state.Status, m.state.Retained)
	}
	for _, path := range []string{
		filepath.Join(cache, "kopia.repository"),
		filepath.Join(cache, "kopia.blobcfg"),
	} {
		if _, err := os.Lstat(path); err != nil {
			t.Fatalf("recognized historical cache entry was lost: %s", path)
		}
	}
	if _, err := os.Lstat(filepath.Join(LegacyConfigDir(root), "repo-35-nas-9964eb569383e921.config.lock")); !os.IsNotExist(err) {
		t.Fatal("historical cache lock remains")
	}
}

func TestMigrationRejectsExternalRepositoryAliasIntoCache(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(LegacyConfigDir(root), "repo-43.config")
	cache := CacheDir(root, source)
	put(t, filepath.Join(cache, "CACHEDIR.TAG"), []byte(cacheMarker+"\n"))
	put(t, filepath.Join(cache, "kopia.repository"), []byte(`{"format":"kopia"}`))

	external := filepath.Join(t.TempDir(), "repository")
	if err := os.Symlink(cache, external); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	data, err := json.Marshal(map[string]any{
		"storage": map[string]any{
			"type":   "filesystem",
			"config": map[string]any{"path": external},
		},
		"caching": map[string]any{"cacheDirectory": filepath.Base(cache)},
	})
	if err != nil {
		t.Fatal(err)
	}
	put(t, source, data)

	err = testMigrator(t, root).run(context.Background())
	var incomplete *IncompleteError
	if !errors.As(err, &incomplete) {
		t.Fatalf("external repository alias into cache was accepted: %v", err)
	}
	if _, err := os.Lstat(ResolveConfig(root, source)); !os.IsNotExist(err) {
		t.Fatal("unsafe repository config was migrated")
	}
	if _, err := os.Lstat(source); err != nil {
		t.Fatalf("original repository config was not retained: %v", err)
	}
}

func TestInventoryRecognizesKopiaTemporaryBlobFiles(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(LegacyConfigDir(root), "repo-34-nas-0123456789abcdef.config")
	seedConnection(t, root, filepath.Base(source))
	cache := CacheDir(root, source)
	put(t, filepath.Join(cache, "blob-list", "xe.f.tmp.bb28d3aefdd5e2cd"), []byte("partial"))
	put(t, filepath.Join(cache, "blob-list", "xr.f.tmp.8fb06e94d265826c"), []byte("partial"))
	if err := testMigrator(t, root).run(context.Background()); err != nil {
		t.Fatalf("Kopia temporary blob files were retained: %v", err)
	}
}

func TestMigrationResumesAtEveryDurableBoundary(t *testing.T) {
	for _, stage := range []string{"discovered", "target_written", "verified", "references_switched", "source_removed"} {
		t.Run(stage, func(t *testing.T) {
			root := t.TempDir()
			seedConnection(t, root, "repo-7.config")
			source := filepath.Join(LegacyConfigDir(root), "repo-7.config")
			m := testMigrator(t, root)
			interrupted := errors.New("simulated process interruption")
			m.after = func(current string) error {
				if stage == current {
					return interrupted
				}
				return nil
			}
			if err := m.run(context.Background()); !errors.Is(err, interrupted) {
				t.Fatalf("fault not injected at %s: %v", stage, err)
			}
			_, oldErr := os.Stat(source)
			_, newErr := os.Stat(ResolveConfig(root, source))
			if oldErr != nil && newErr != nil {
				t.Fatal("both usable copies were lost")
			}
			resumed := testMigrator(t, root)
			if err := resumed.run(context.Background()); err != nil {
				t.Fatal(err)
			}
			if resumed.state.Status != "completed" {
				t.Fatal("migration did not resume deterministically")
			}
			if err := testMigrator(t, root).run(context.Background()); err != nil {
				t.Fatalf("repeat failed: %v", err)
			}
		})
	}
}

func TestMigrationPreservesConflictsUnknownFilesAndRealRepositories(t *testing.T) {
	for _, scenario := range []string{"destination-conflict", "unknown-file", "malformed-config", "repository-in-cache", "symlink"} {
		t.Run(scenario, func(t *testing.T) {
			root := t.TempDir()
			source := filepath.Join(LegacyConfigDir(root), "repo-7.config")
			old := seedConnection(t, root, "repo-7.config")
			switch scenario {
			case "destination-conflict":
				put(t, ResolveConfig(root, source), []byte("unrelated existing target"))
			case "unknown-file":
				put(t, filepath.Join(root, "cache", "user-data.config"), []byte("unique user data"))
			case "malformed-config":
				put(t, source, []byte("not a Kopia connection"))
			case "repository-in-cache":
				data, _ := json.Marshal(map[string]any{
					"storage": map[string]any{"type": "filesystem", "config": map[string]any{"path": filepath.Join(root, "cache", "business")}},
					"caching": map[string]any{"cacheDirectory": "warm"},
				})
				put(t, source, data)
			case "symlink":
				external := filepath.Join(t.TempDir(), "business-data")
				put(t, external, []byte("unique"))
				if err := os.Symlink(external, filepath.Join(root, "cache", "user-link")); err != nil {
					t.Skipf("symlinks unavailable: %v", err)
				}
			}
			m := testMigrator(t, root)
			err := m.run(context.Background())
			if err == nil {
				t.Fatal("unsafe inventory declared disposable")
			}
			if scenario == "destination-conflict" {
				got, _ := os.ReadFile(source)
				if !bytes.Equal(got, old) {
					t.Fatal("conflict destroyed the original")
				}
			} else {
				var incomplete *IncompleteError
				if !errors.As(err, &incomplete) || m.state.Status != "incomplete" {
					t.Fatalf("expected retained inventory: %v", err)
				}
			}
		})
	}
}

func TestReferenceFailureAndChangedReplacementRetainOriginal(t *testing.T) {
	for _, scenario := range []string{"reference-failure", "replacement-changed"} {
		t.Run(scenario, func(t *testing.T) {
			root := t.TempDir()
			source := filepath.Join(LegacyConfigDir(root), "repo-9.config")
			old := seedConnection(t, root, "repo-9.config")
			m := testMigrator(t, root)
			m.switchReferences = func(context.Context, map[string]string) error {
				if scenario == "reference-failure" {
					return errors.New("database unavailable")
				}
				put(t, ResolveConfig(root, source), []byte("changed by another writer"))
				return nil
			}
			if err := m.run(context.Background()); err == nil {
				t.Fatal("unsafe cleanup succeeded")
			}
			data, err := os.ReadFile(source)
			if err != nil || !bytes.Equal(data, old) {
				t.Fatal("only usable original was removed")
			}
		})
	}
}

func TestMigrationDoesNotTouchActiveUsers(t *testing.T) {
	root := t.TempDir()
	old := seedConnection(t, root, "repo-1.config")
	m := testMigrator(t, root)
	m.checkUsers = func(context.Context, string, bool) error { return errors.New("active backup") }
	if err := m.run(context.Background()); err == nil {
		t.Fatal("migration ignored active users")
	}
	got, _ := os.ReadFile(filepath.Join(LegacyConfigDir(root), "repo-1.config"))
	if !bytes.Equal(got, old) {
		t.Fatal("active connection was modified")
	}
}

func TestRecoveryCopyAndJournalSurviveCacheRemovalDuringInterruption(t *testing.T) {
	root := t.TempDir()
	seedConnection(t, root, "repo-1.config")
	m := testMigrator(t, root)
	m.after = func(stage string) error {
		if stage == "target_written" {
			return errors.New("interrupted before verification journal update")
		}
		return nil
	}
	if err := m.run(context.Background()); err == nil {
		t.Fatal("interruption was not injected")
	}
	if err := os.RemoveAll(filepath.Join(root, "cache")); err != nil {
		t.Fatal(err)
	}
	if err := testMigrator(t, root).run(context.Background()); err != nil {
		t.Fatalf("durable replacement could not be recovered: %v", err)
	}
}

func TestCleanupRechecksOwnershipAfterCompletedMigration(t *testing.T) {
	root := t.TempDir()
	if err := testMigrator(t, root).run(context.Background()); err != nil {
		t.Fatal(err)
	}
	unique := filepath.Join(root, "cache", "user-file.config")
	put(t, unique, []byte("unique business data"))
	if err := ClearCache(context.Background(), root); err == nil {
		t.Fatal("new unclassified files were deleted using stale completion evidence")
	}
	if data, err := os.ReadFile(unique); err != nil || string(data) != "unique business data" {
		t.Fatal("unclassified data was lost")
	}
	status, err := Status(root)
	if err != nil || status["disposable"] != false {
		t.Fatal("incomplete inventory was advertised as disposable")
	}
}

func TestLogAliasCannotPlaceLogsBackInsideCache(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "cache"), 0o700); err != nil {
		t.Fatal(err)
	}
	alias := filepath.Join(t.TempDir(), "log-parent")
	if err := os.Symlink(filepath.Join(root, "cache"), alias); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if _, err := ProcessEnv(root, filepath.Join(alias, "logs"), nil); err == nil {
		t.Fatal("physical log/cache overlap was accepted")
	}
}

func TestConfiguredInstallationAliasPreservesHistoricalCacheIdentity(t *testing.T) {
	actual := t.TempDir()
	root := filepath.Join(t.TempDir(), "agent-root")
	if err := os.Symlink(actual, root); err != nil {
		t.Skipf("directory aliases unavailable: %v", err)
	}
	seedConnection(t, root, "repo-1.config")
	old := filepath.Join(LegacyConfigDir(root), "repo-1.config")
	cache := CacheDir(root, old)
	if err := testMigrator(t, root).run(context.Background()); err != nil {
		t.Fatal(err)
	}
	if CacheDir(root, ResolveConfig(root, old)) != cache {
		t.Fatal("configured root alias changed the historical cache identity")
	}
}

func TestCacheIdentityRemainsIsolatedAndIndependentOfConfigMove(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{"repo-1.config", "repo-1-nas-0123456789abcdef.config", "repo-1-server-session-0123456789ab.config"} {
		old := filepath.Join(LegacyConfigDir(root), name)
		next := ResolveConfig(root, old)
		if old == next || CacheDir(root, old) != CacheDir(root, next) {
			t.Fatalf("cache identity changed for %s", name)
		}
		if CacheDir(root, old) == CacheDir(root, filepath.Join(ConfigDir(root), "repo-2.config")) {
			t.Fatal("unrelated repository caches were shared")
		}
	}
}

func TestLogAliasIsRetiredOnlyAfterVerifiedLogCopy(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "cache", "kopia", "cli-logs")
	name := "kopia-20261109-000000-123-repository-status.log"
	put(t, filepath.Join(dir, name), []byte("historical log"))
	if err := os.Symlink(name, filepath.Join(dir, "latest.log")); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if err := testMigrator(t, root).run(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Lstat(filepath.Join(dir, "latest.log")); !os.IsNotExist(err) {
		t.Fatal("historical log alias remains")
	}
}
