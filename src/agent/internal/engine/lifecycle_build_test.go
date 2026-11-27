package engine

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"runtime"
	"testing"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/install"
)

func TestUpgradeRejectsWrongCommitBeforeStaging(t *testing.T) {
	stubUpgradeDownloadDependencies(t)
	stubUpgradeSpaceCheck(t, func(string, string, uint64) error { return nil })
	root := t.TempDir()
	files := map[string][]byte{"bin/hfl-agent": []byte("agent"), "bin/kopia": []byte("kopia"), "install.sh": []byte("installer")}
	kopia := "bin/kopia"
	if runtime.GOOS == "windows" {
		files = map[string][]byte{
			"bin/hfl-agent.exe":               []byte("agent"),
			"bin/kopia.exe":                   []byte("kopia"),
			"bin/hfl-agent-user-launcher.exe": []byte("launcher"),
			"install.ps1":                     []byte("installer"),
			"install.cmd":                     []byte("wrapper"),
		}
		kopia = "bin/kopia.exe"
	}
	sums := map[string]string{}
	for name, data := range files {
		sums[name] = fmt.Sprintf("sha256:%x", sha256.Sum256(data))
	}
	manifest, err := json.Marshal(map[string]any{
		"schema": 1, "agent_version": "1.2.3", "agent_commit": "1234abcd", "platform": runtime.GOOS, "arch": runtime.GOARCH,
		"bundle_flavor": "standard", "kopia_binary_sha256": sums[kopia], "files": sums,
	})
	if err != nil {
		t.Fatal(err)
	}
	files["MANIFEST.json"] = manifest
	var archive bytes.Buffer
	if runtime.GOOS == "windows" {
		w := zip.NewWriter(&archive)
		for name, data := range files {
			entry, err := w.Create("hfl-agent-1.2.3/" + name)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := entry.Write(data); err != nil {
				t.Fatal(err)
			}
		}
		if err := w.Close(); err != nil {
			t.Fatal(err)
		}
	} else {
		gz := gzip.NewWriter(&archive)
		w := tar.NewWriter(gz)
		for name, data := range files {
			if err := w.WriteHeader(&tar.Header{Name: "hfl-agent-1.2.3/" + name, Typeflag: tar.TypeReg, Mode: 0700, Size: int64(len(data))}); err != nil {
				t.Fatal(err)
			}
			if _, err := w.Write(data); err != nil {
				t.Fatal(err)
			}
		}
		if err := w.Close(); err != nil {
			t.Fatal(err)
		}
		if err := gz.Close(); err != nil {
			t.Fatal(err)
		}
	}
	downloadUpgradeArchive = func(_ context.Context, _ string, path string, _ install.ProgressReporter) error {
		return os.WriteFile(path, archive.Bytes(), 0600)
	}
	e := New(staticConfigProvider{cfg: &model.AgentConfig{Role: model.RoleAgent, DataDir: root}})
	status, result, _ := e.runAgentUpgrade(t.Context(), ReporterSink{}, "wrong-build", Payload{Extra: map[string]any{
		"download_url": "https://example.invalid/package", "target_version": "1.2.3", "target_commit": "4567abcd",
	}})
	if status != "failed" || result["error_code"] != "AGENT_UPGRADE_BUILD_MISMATCH" || result["old_agent_unchanged"] != true {
		t.Fatalf("status=%q result=%#v", status, result)
	}
	if _, err := os.Stat(install.LifecycleUpgradeDir(root)); !os.IsNotExist(err) {
		t.Fatal("wrong build was staged or scheduled")
	}
}
