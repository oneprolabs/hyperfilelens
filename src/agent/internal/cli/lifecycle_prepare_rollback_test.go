package cli

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	goruntime "runtime"
	"strings"
	"testing"
	"time"

	"hyperfilelens/agent/internal/infra/database"
)

func TestRollbackArchiveRequiresBothFilesAndRejectsDuplicateOrUnlistedFiles(t *testing.T) {
	for _, kind := range []string{"latest.tar.gz", "latest.zip"} {
		for _, tc := range []struct {
			name  string
			names []string
			want  string
		}{
			{"complete", []string{"config/agent.env", "data/agent.db"}, ""},
			{"missing database", []string{"config/agent.env"}, "requires both"},
			{"missing environment", []string{"data/agent.db"}, "requires both"},
			{"duplicate", []string{"config/agent.env", "data/agent.db", "data/agent.db"}, "duplicate"},
			{"unexpected path", []string{"data/agent.db", "../agent.env"}, "unexpected"},
		} {
			t.Run(kind+"/"+tc.name, func(t *testing.T) {
				root := t.TempDir()
				archive := filepath.Join(root, kind)
				writeTestRollbackArchive(t, archive, tc.names, map[string][]byte{"config/agent.env": []byte("HFL_NODE_ROLE=agent\n"), "data/agent.db": []byte("database-in-fixture"), "../agent.env": []byte("unexpected")})
				stage := filepath.Join(root, "stage")
				if err := os.Mkdir(stage, 0700); err != nil {
					t.Fatal(err)
				}
				f, err := os.Open(archive)
				if err != nil {
					t.Fatal(err)
				}
				defer f.Close()
				err = stageRollbackArchive(t.Context(), stage, f, kind)
				if tc.want == "" && err != nil || tc.want != "" && (err == nil || !strings.Contains(err.Error(), tc.want)) {
					t.Fatalf("error=%v, want %q", err, tc.want)
				}
			})
		}
	}
}

func TestPrepareRollbackRejectsMissingDbCorruptDbAndBinaryMismatchWithoutLiveMutation(t *testing.T) {
	for _, tc := range []string{"complete", "missing-db", "corrupt-db", "bad-binary"} {
		t.Run(tc, func(t *testing.T) {
			root := t.TempDir()
			work := t.TempDir()
			write := func(rel string, data []byte) {
				t.Helper()
				p := filepath.Join(root, rel)
				if err := os.MkdirAll(filepath.Dir(p), 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(p, data, 0600); err != nil {
					t.Fatal(err)
				}
			}
			live := []byte("live database remains untouched")
			write("data/agent.db", live)
			names := map[string]string{"hfl-agent": "bin/hfl-agent", "kopia": "bin/kopia", "install.sh": "install.sh"}
			archiveName := "latest.tar.gz"
			if goruntime.GOOS == "windows" {
				names = map[string]string{"hfl-agent.exe": "bin/hfl-agent.exe", "kopia.exe": "bin/kopia.exe", "install.ps1": "install.ps1", "install.cmd": "install.cmd"}
				archiveName = "latest.zip"
			}
			files := map[string]string{}
			for fname, manifestName := range names {
				data := []byte("fixture: " + fname)
				files[manifestName] = fmt.Sprintf("sha256:%x", sha256.Sum256(data))
				if tc == "bad-binary" && strings.HasPrefix(fname, "hfl-agent") {
					data = []byte("changed")
				}
				write("backup/rollback/bin/"+fname, data)
			}
			manifest, err := json.Marshal(map[string]any{"schema": 1, "agent_version": "old", "agent_commit": "abcd", "files": files})
			if err != nil {
				t.Fatal(err)
			}
			write("backup/rollback/bin/MANIFEST.json", manifest)
			write("backup/rollback/bin/INSTALLED_VERSION", []byte("\ufeffold\n"))
			write("backup/rollback/meta.json", []byte("\ufeff{\"previous_version\":\"old\"}"))
			snapshot := filepath.Join(t.TempDir(), "snapshot.db")
			db, err := database.Open(t.Context(), snapshot)
			if err != nil {
				t.Fatal(err)
			}
			if err := db.Close(); err != nil {
				t.Fatal(err)
			}
			raw, err := os.ReadFile(snapshot)
			if err != nil {
				t.Fatal(err)
			}
			if tc == "corrupt-db" {
				raw = []byte("not sqlite")
			}
			entries := []string{"config/agent.env", "data/agent.db"}
			if tc == "missing-db" {
				entries = entries[:1]
			}
			archive := filepath.Join(root, "backup/rollback", archiveName)
			writeTestRollbackArchive(t, archive, entries, map[string][]byte{"config/agent.env": []byte("HFL_NODE_ROLE=agent\n"), "data/agent.db": raw})
			err = prepareRollback(t.Context(), root, "old", work, "none", false, -1)
			if got, readErr := os.ReadFile(filepath.Join(root, "data/agent.db")); readErr != nil || string(got) != string(live) {
				t.Fatalf("preparation touched live DB: %v", readErr)
			}
			if tc == "complete" {
				if err != nil {
					t.Fatal(err)
				}
				if got, readErr := os.ReadFile(filepath.Join(work, "rollback-ready/data/agent.db")); readErr != nil || string(got) != string(raw) {
					t.Fatalf("staged DB invalid: %v", readErr)
				}
			} else {
				if err == nil {
					t.Fatal("unverified rollback input accepted")
				}
				if _, statErr := os.Stat(filepath.Join(work, "rollback-ready")); !os.IsNotExist(statErr) {
					t.Fatal("partial workspace was left eligible for restore")
				}
			}
		})
	}
}

func writeTestRollbackArchive(t *testing.T, path string, names []string, values map[string][]byte) {
	t.Helper()
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := f.Close(); err != nil {
			t.Fatal(err)
		}
	}()
	if strings.HasSuffix(path, ".zip") {
		z := zip.NewWriter(f)
		for _, name := range names {
			h := zip.FileHeader{Name: name, Method: zip.Deflate}
			h.SetMode(0600)
			w, err := z.CreateHeader(&h)
			if err != nil {
				t.Fatal(err)
			}
			if _, err = w.Write(values[name]); err != nil {
				t.Fatal(err)
			}
		}
		if err := z.Close(); err != nil {
			t.Fatal(err)
		}
		return
	}
	gz := gzip.NewWriter(f)
	tw := tar.NewWriter(gz)
	for _, name := range names {
		if err := tw.WriteHeader(&tar.Header{Name: "./" + name, Typeflag: tar.TypeReg, Mode: 0600, Size: int64(len(values[name]))}); err != nil {
			t.Fatal(err)
		}
		if _, err := tw.Write(values[name]); err != nil {
			t.Fatal(err)
		}
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	if err := gz.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestLifecycleStagedUpgradeRequiresMatchingAuthorizedTask(t *testing.T) {
	root := t.TempDir()
	db, err := database.Open(t.Context(), database.DefaultPath(root))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := database.NewTaskRepo(db)
	name := "package.tar.gz"
	if goruntime.GOOS == "windows" {
		name = "package.zip"
	}
	archive := filepath.Join(root, "lifecycle", "upgrade", name)
	if err := os.MkdirAll(filepath.Dir(archive), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(archive, []byte("fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	args := func(command, path, commit string) []string {
		return []string{command, "--data-dir", root, "--archive", path, "--version", "2.0.0", "--commit", commit}
	}
	standalone := filepath.Join(root, "operator.tar.gz")
	if err := os.WriteFile(standalone, []byte("standalone fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := runLifecycle(args("begin", standalone, "1abc")); err != nil {
		t.Fatal("standalone path unexpectedly requires a task", err)
	}
	if err := runLifecycle(args("begin", archive, "1abc")); err == nil {
		t.Fatal("unbound staged upgrade accepted")
	}
	now := time.Now().UTC()
	accepted := now.Add(-time.Second)
	if err := repo.RecordCommand(t.Context(), database.RecordInput{TaskID: "upgrade", Kind: "agent.upgrade", StartedAt: &accepted, Payload: map[string]any{"target_version": "2.0.0", "target_commit": "1abc"}}); err != nil {
		t.Fatal(err)
	}
	if err := repo.UpdateRunning(t.Context(), "upgrade", map[string]any{"mode": "local_detached", "target_version": "2.0.0", "detached_started_at": now.Format(time.RFC3339Nano)}); err != nil {
		t.Fatal(err)
	}
	if err := runLifecycle(args("begin", archive, "2def")); err == nil {
		t.Fatal("version-equal but wrong build accepted")
	}
	if err := runLifecycle(args("authorize", archive, "2def")); err == nil {
		t.Fatal("Windows authorization path accepted wrong build")
	}
	if err := runLifecycle(args("authorize", archive, "1ABC")); err != nil {
		t.Fatal(err)
	}
	t.Run("root-alias", func(t *testing.T) {
		alias := filepath.Join(t.TempDir(), "agent-alias")
		if err := os.Symlink(root, alias); err != nil {
			t.Skip(err)
		}
		aliasArchive := filepath.Join(alias, "lifecycle", "upgrade", name)
		for _, paths := range [][2]string{{alias, archive}, {root, aliasArchive}, {alias, aliasArchive}} {
			good := args("authorize", paths[1], "1abc")
			good[2] = paths[0]
			if err := runLifecycle(good); err != nil {
				t.Fatal("aliased staged archive could not be authorized", err)
			}
			bad := append([]string(nil), good...)
			bad[len(bad)-1] = "2def"
			if err := runLifecycle(bad); err == nil {
				t.Fatal("alias bypassed managed archive build authorization")
			}
		}
	})
	if _, changed, err := repo.MarkCancelledIfActive(t.Context(), "upgrade"); err != nil || !changed {
		t.Fatalf("cancel: %v %v", changed, err)
	}
	if err := runLifecycle(args("authorize", archive, "1abc")); err == nil {
		t.Fatal("cancelled task authorized deployment")
	}
}
