package enroll

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"hyperfilelens/agent/internal/model"
)

func TestValidateAgentPackageFor(t *testing.T) {
	root := writeTestAgentPackage(t, "linux", "amd64", "standard")
	if err := validateAgentPackageFor(root, "linux", "amd64", model.RoleAgent, "1.2.3"); err != nil {
		t.Fatal(err)
	}
}

func TestValidateAgentPackageRejectsPlatformMismatch(t *testing.T) {
	root := writeTestAgentPackage(t, "linux", "amd64", "standard")
	if err := validateAgentPackageFor(root, "darwin", "amd64", model.RoleAgent, "1.2.3"); err == nil {
		t.Fatal("expected platform mismatch")
	}
}

func TestValidateAgentPackageRejectsTampering(t *testing.T) {
	root := writeTestAgentPackage(t, "linux", "amd64", "standard")
	if err := os.WriteFile(filepath.Join(root, "bin", "kopia"), []byte("tampered"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := validateAgentPackageFor(root, "linux", "amd64", model.RoleAgent, "1.2.3"); err == nil {
		t.Fatal("expected checksum failure")
	}
}

func TestValidateAgentPackageForWindows(t *testing.T) {
	root := writeTestAgentPackage(t, "windows", "amd64", "standard")
	if err := validateAgentPackageFor(root, "windows", "amd64", model.RoleAgent, "v1.2.3"); err != nil {
		t.Fatal(err)
	}
}

func TestValidateAgentPackageRejectsNonStandardSourceHost(t *testing.T) {
	root := writeTestAgentPackage(t, "linux", "amd64", "ubuntu2404")
	if err := validateAgentPackageFor(root, "linux", "amd64", model.RoleAgent, "1.2.3"); err == nil {
		t.Fatal("expected non-standard Source Host package to be rejected")
	}
}

func TestValidateAgentPackageRejectsMissingBuildCommit(t *testing.T) {
	root := writeTestAgentPackage(t, "linux", "amd64", "standard")
	manifestPath := filepath.Join(root, "MANIFEST.json")
	manifest, err := readAgentPackageManifest(root)
	if err != nil {
		t.Fatal(err)
	}
	manifest.AgentCommit = ""
	raw, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(manifestPath, raw, 0o600); err != nil {
		t.Fatal(err)
	}

	if err := validateAgentPackageFor(root, "linux", "amd64", model.RoleAgent, "1.2.3"); err == nil {
		t.Fatal("expected missing build commit to be rejected")
	}
}

func TestInstalledAgentBuildIdentityUsesOneManifest(t *testing.T) {
	root := t.TempDir()
	t.Setenv("HFL_DATA_DIR", root)
	manifest := agentPackageManifest{
		Schema:       1,
		AgentVersion: "1.2.3",
		AgentCommit:  "abc123",
	}
	raw, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "MANIFEST.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}

	identity, err := installedAgentBuildIdentity("v1.2.3")
	if err != nil {
		t.Fatal(err)
	}
	if identity.Version != "1.2.3" || identity.Commit != "abc123" {
		t.Fatalf("identity=%#v", identity)
	}
}

func TestInstalledAgentBuildIdentityRejectsMissingManifest(t *testing.T) {
	root := t.TempDir()
	t.Setenv("HFL_DATA_DIR", root)

	if _, err := installedAgentBuildIdentity("1.2.3"); err == nil {
		t.Fatal("expected a missing installed manifest to be rejected")
	}
}

func TestInstalledAgentBuildIdentityRejectsVersionMismatch(t *testing.T) {
	root := t.TempDir()
	t.Setenv("HFL_DATA_DIR", root)
	manifest := agentPackageManifest{
		Schema:       1,
		AgentVersion: "1.2.3",
		AgentCommit:  "abc123",
	}
	raw, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "MANIFEST.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}

	if _, err := installedAgentBuildIdentity("1.2.4"); err == nil {
		t.Fatal("expected installed binary and manifest version mismatch to be rejected")
	}
}

func writeTestAgentPackage(t *testing.T, platform, arch, flavor string) string {
	t.Helper()
	root := t.TempDir()
	files := map[string][]byte{}
	kopiaName := "bin/kopia"
	if platform == "windows" {
		files["bin/hfl-agent.exe"] = []byte("agent")
		files["bin/hfl-agent-user-launcher.exe"] = []byte("launcher")
		files["bin/kopia.exe"] = []byte("kopia")
		files["install.ps1"] = []byte("# installer\n")
		files["install.cmd"] = []byte("@echo off\r\n")
		kopiaName = "bin/kopia.exe"
	} else {
		files["bin/hfl-agent"] = []byte("agent")
		files[kopiaName] = []byte("kopia")
		files["install.sh"] = []byte("#!/bin/sh\n")
	}
	checksums := map[string]string{}
	for name, data := range files {
		path := filepath.Join(root, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, data, 0o700); err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256(data)
		checksums[name] = "sha256:" + hex.EncodeToString(sum[:])
	}
	manifest := agentPackageManifest{
		Schema:          1,
		AgentVersion:    "1.2.3",
		AgentCommit:     "abc123",
		Platform:        platform,
		Arch:            arch,
		BundleFlavor:    flavor,
		KopiaBinaryHash: checksums[kopiaName],
		Files:           checksums,
	}
	raw, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "MANIFEST.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}
	return root
}
