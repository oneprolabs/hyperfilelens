package enroll

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/selfupdate"
)

type agentPackageManifest struct {
	Schema          int               `json:"schema"`
	AgentVersion    string            `json:"agent_version"`
	AgentCommit     string            `json:"agent_commit"`
	Platform        string            `json:"platform"`
	Arch            string            `json:"arch"`
	BundleFlavor    string            `json:"bundle_flavor"`
	KopiaBinaryHash string            `json:"kopia_binary_sha256"`
	Files           map[string]string `json:"files"`
}

func installedAgentBuildIdentity(installedVersion string) (selfupdate.BuildIdentity, error) {
	manifest, err := readAgentPackageManifest(dataDirForAgent())
	if err != nil {
		return selfupdate.BuildIdentity{}, err
	}
	manifestVersion := strings.TrimSpace(manifest.AgentVersion)
	manifestCommit := strings.TrimSpace(manifest.AgentCommit)
	if manifestVersion == "" || manifestCommit == "" {
		return selfupdate.BuildIdentity{}, fmt.Errorf("installed package manifest is missing the Agent build identity")
	}
	installedVersion = strings.TrimPrefix(strings.TrimSpace(installedVersion), "v")
	if installedVersion != "" && strings.TrimPrefix(manifestVersion, "v") != installedVersion {
		return selfupdate.BuildIdentity{}, fmt.Errorf(
			"installed Agent version %s does not match package manifest version %s",
			installedVersion,
			manifestVersion,
		)
	}
	return selfupdate.BuildIdentity{Version: manifestVersion, Commit: manifestCommit}, nil
}

func readAgentPackageManifest(root string) (agentPackageManifest, error) {
	manifestPath := filepath.Join(root, "MANIFEST.json")
	raw, err := os.ReadFile(manifestPath)
	if err != nil {
		return agentPackageManifest{}, fmt.Errorf("read package manifest: %w", err)
	}
	var manifest agentPackageManifest
	if err := json.Unmarshal(raw, &manifest); err != nil {
		return agentPackageManifest{}, fmt.Errorf("parse package manifest: %w", err)
	}
	return manifest, nil
}

func validateAgentPackage(root string, role model.Role, expectedVersion string) error {
	return validateAgentPackageFor(root, runtime.GOOS, runtime.GOARCH, role, expectedVersion)
}

func validateAgentPackageFor(root, platform, arch string, role model.Role, expectedVersion string) error {
	manifest, err := readAgentPackageManifest(root)
	if err != nil {
		return err
	}
	if manifest.Schema != 1 {
		return fmt.Errorf("unsupported package manifest schema %d", manifest.Schema)
	}
	if strings.TrimSpace(manifest.AgentVersion) == "" || strings.TrimSpace(manifest.AgentCommit) == "" {
		return fmt.Errorf("package manifest is missing the Agent build identity")
	}
	if manifest.Platform != platform || manifest.Arch != arch {
		return fmt.Errorf(
			"package platform mismatch: package is %s/%s, host is %s/%s",
			manifest.Platform, manifest.Arch, platform, arch,
		)
	}
	if expectedVersion = strings.TrimPrefix(strings.TrimSpace(expectedVersion), "v"); expectedVersion != "" && manifest.AgentVersion != expectedVersion {
		return fmt.Errorf("package version mismatch: expected %s, got %s", expectedVersion, manifest.AgentVersion)
	}
	if role == model.RoleAgent && manifest.BundleFlavor != "standard" {
		return fmt.Errorf("Source Host requires a standard Agent package, got %q", manifest.BundleFlavor)
	}
	if len(manifest.Files) == 0 {
		return fmt.Errorf("package manifest does not contain file checksums")
	}

	required := []string{"bin/hfl-agent", "bin/kopia"}
	if platform == "windows" {
		required = []string{
			"bin/hfl-agent.exe",
			"bin/hfl-agent-user-launcher.exe",
			"bin/kopia.exe",
			"install.ps1",
			"install.cmd",
		}
	} else {
		required = append(required, "install.sh")
	}
	for _, name := range required {
		if _, ok := manifest.Files[name]; !ok {
			return fmt.Errorf("package manifest is missing required file %s", name)
		}
	}

	seen := map[string]bool{}
	err = filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			return nil
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("package contains unsupported symlink %s", path)
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		rel = filepath.ToSlash(rel)
		if rel == "MANIFEST.json" {
			return nil
		}
		expected, ok := manifest.Files[rel]
		if !ok {
			return fmt.Errorf("package file is not covered by the manifest: %s", rel)
		}
		actual, err := fileSHA256(path)
		if err != nil {
			return fmt.Errorf("checksum %s: %w", rel, err)
		}
		expected = strings.TrimPrefix(strings.ToLower(strings.TrimSpace(expected)), "sha256:")
		if expected != actual {
			return fmt.Errorf("package checksum mismatch for %s", rel)
		}
		seen[rel] = true
		return nil
	})
	if err != nil {
		return err
	}
	for name := range manifest.Files {
		if !seen[name] {
			return fmt.Errorf("package manifest references missing file %s", name)
		}
	}

	kopiaName := "bin/kopia"
	if platform == "windows" {
		kopiaName = "bin/kopia.exe"
	}
	manifestKopiaHash := strings.TrimPrefix(strings.ToLower(strings.TrimSpace(manifest.KopiaBinaryHash)), "sha256:")
	fileKopiaHash := strings.TrimPrefix(strings.ToLower(strings.TrimSpace(manifest.Files[kopiaName])), "sha256:")
	if manifestKopiaHash == "" || manifestKopiaHash != fileKopiaHash {
		return fmt.Errorf("Kopia checksum metadata does not match %s", kopiaName)
	}
	return nil
}

func fileSHA256(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}
