package cli

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"context"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	goruntime "runtime"
	"strings"

	"hyperfilelens/agent/internal/infra/database"
	"hyperfilelens/agent/internal/platform/lifecycle"
)

const maxRollbackSmallFile = 8 << 20
const maxRollbackDatabase = 8 << 30

// prepareRollback is deliberately separate from verifyRollbackAs. The latter
// compares a restored installation against the snapshot. This operation proves
// and copies what would be restored, before the installer may stop or mutate
// the current installation. Restore consumes only this private staged copy.
func prepareRollback(ctx context.Context, root, version, workspace, service string, active bool, uid int64) (retErr error) {
	if strings.TrimSpace(version) == "" {
		return fmt.Errorf("rollback previous version is missing")
	}
	stage, err := lifecycle.Path(workspace, "rollback-ready")
	if err != nil {
		return err
	}
	if err := os.Mkdir(stage, 0700); err != nil {
		return fmt.Errorf("create private rollback staging: %w", err)
	}
	defer func() {
		if retErr != nil {
			_ = os.RemoveAll(stage)
		}
	}()
	rollbackPath, err := lifecycle.VerificationPath(root, "backup/rollback", uid)
	if err != nil {
		return err
	}
	backup, err := os.OpenRoot(rollbackPath)
	if err != nil {
		return fmt.Errorf("open rollback snapshot: %w", err)
	}
	defer backup.Close()
	// Keep reads anchored to the rollback directory even when an installed
	// account's work directories are writable by the service principal.
	open := func(relative string) (*os.File, error) {
		if _, err := lifecycle.VerificationPath(root, filepath.Join("backup/rollback", relative), uid); err != nil {
			return nil, err
		}
		info, err := backup.Lstat(relative)
		if err != nil {
			return nil, err
		}
		if !info.Mode().IsRegular() {
			return nil, fmt.Errorf("invalid rollback file type: %s", relative)
		}
		f, err := backup.Open(relative)
		if err != nil {
			return nil, err
		}
		opened, err := f.Stat()
		if err != nil || !os.SameFile(info, opened) {
			f.Close()
			return nil, fmt.Errorf("rollback file changed while opening: %s", relative)
		}
		return f, nil
	}
	copyItem := func(name string, limit int64, mode fs.FileMode, optional bool) (bool, error) {
		f, err := open(name)
		if optional && os.IsNotExist(err) {
			return false, nil
		}
		if err != nil {
			return false, fmt.Errorf("rollback snapshot missing or unsafe %s: %w", name, err)
		}
		defer f.Close()
		return true, copyRollbackFile(ctx, stage, name, f, limit, mode, -1)
	}
	if _, err := copyItem("meta.json", 1<<20, 0600, false); err != nil {
		return err
	}
	var meta struct {
		PreviousVersion string `json:"previous_version"`
	}
	if err := readPreparedRollbackJSON(stage, "meta.json", &meta); err != nil || !sameRollbackVersion(meta.PreviousVersion, version) {
		return fmt.Errorf("rollback metadata does not match the previous version")
	}
	for _, name := range []string{"MANIFEST.json", "INSTALLED_VERSION"} {
		if _, err := copyItem(filepath.Join("bin", name), 1<<20, 0600, false); err != nil {
			return err
		}
	}
	var manifest struct {
		Schema  int               `json:"schema"`
		Version string            `json:"agent_version"`
		Commit  string            `json:"agent_commit"`
		Files   map[string]string `json:"files"`
	}
	if err := readPreparedRollbackJSON(stage, "bin/MANIFEST.json", &manifest); err != nil || manifest.Schema != 1 ||
		!sameRollbackVersion(manifest.Version, version) || strings.TrimSpace(manifest.Commit) == "" {
		return fmt.Errorf("rollback binary manifest does not describe the previous build")
	}
	installed, err := os.ReadFile(filepath.Join(stage, "bin", "INSTALLED_VERSION"))
	if err != nil || !sameRollbackVersion(string(installed), version) {
		return fmt.Errorf("rollback installed-version marker does not match the previous build")
	}
	required := map[string]string{"hfl-agent": "bin/hfl-agent", "kopia": "bin/kopia", "install.sh": "install.sh"}
	optional := []string{"run-agent.sh"}
	archiveName := "latest.tar.gz"
	if goruntime.GOOS == "windows" {
		archiveName = "latest.zip"
		required = map[string]string{"hfl-agent.exe": "bin/hfl-agent.exe", "kopia.exe": "bin/kopia.exe", "install.ps1": "install.ps1", "install.cmd": "install.cmd"}
		if _, ok := manifest.Files["bin/hfl-agent-user-launcher.exe"]; ok {
			required["hfl-agent-user-launcher.exe"] = "bin/hfl-agent-user-launcher.exe"
		}
		// uninstall.cmd is present in Windows distribution manifests. Preserve
		// the optional generated launcher separately.
		if _, ok := manifest.Files["uninstall.cmd"]; ok {
			required["uninstall.cmd"] = "uninstall.cmd"
		}
		optional = []string{"run-agent.ps1"}
	}
	for manifestName := range manifest.Files {
		if strings.HasPrefix(manifestName, "libexec/") {
			clean := filepath.ToSlash(filepath.Clean(manifestName))
			if clean != manifestName || strings.Contains(manifestName, "\\") || len(required) >= 128 {
				return fmt.Errorf("invalid rollback manifest libexec entries")
			}
			required[manifestName] = manifestName
		}
	}
	for name, manifestName := range required {
		want, err := hex.DecodeString(strings.TrimPrefix(strings.ToLower(strings.TrimSpace(manifest.Files[manifestName])), "sha256:"))
		if err != nil || len(want) != 32 {
			return fmt.Errorf("rollback manifest lacks a valid checksum for %s", manifestName)
		}
		rel := filepath.Join("bin", name)
		if _, err := copyItem(rel, 1<<30, 0755, false); err != nil {
			return err
		}
		hash, err := fileDigest(ctx, filepath.Join(stage, rel))
		if err != nil || hex.EncodeToString(hash[:]) != hex.EncodeToString(want) {
			return fmt.Errorf("rollback checksum verification failed for %s", manifestName)
		}
	}
	for _, name := range optional {
		present, err := copyItem(filepath.Join("bin", name), maxRollbackSmallFile, 0755, true)
		if err != nil {
			return err
		}
		if !present && active && service == "launchd" {
			return fmt.Errorf("rollback launchd launcher missing")
		}
	}
	if err := prepareRollbackService(stage, service, active, copyItem); err != nil {
		return err
	}
	archive, err := open(archiveName)
	if err != nil {
		return fmt.Errorf("open rollback state archive: %w", err)
	}
	defer archive.Close()
	if err := stageRollbackArchive(ctx, stage, archive, archiveName); err != nil {
		return err
	}
	path := filepath.Join(stage, "data", "agent.db")
	if err := checkRollbackDatabase(ctx, path); err != nil {
		return fmt.Errorf("rollback state database failed integrity verification: %w", err)
	}
	if err := database.CheckUpgradeSnapshotSchema(ctx, path); err != nil {
		return err
	}
	return nil
}

func readPreparedRollbackJSON(stage, relative string, output any) error {
	raw, err := os.ReadFile(filepath.Join(stage, relative))
	if err != nil {
		return err
	}
	// Windows PowerShell writes UTF-8 BOMs for Set-Content -Encoding UTF8.
	return json.Unmarshal(bytes.TrimPrefix(raw, []byte("\xef\xbb\xbf")), output)
}

func sameRollbackVersion(a, b string) bool {
	clean := func(s string) string {
		return strings.TrimPrefix(strings.TrimSpace(strings.TrimPrefix(s, "\ufeff")), "v")
	}
	return clean(a) == clean(b)
}

func prepareRollbackService(stage, service string, active bool, copyItem func(string, int64, fs.FileMode, bool) (bool, error)) error {
	if err := os.Mkdir(filepath.Join(stage, "service"), 0700); err != nil {
		return err
	}
	var name, absent string
	switch service {
	case "systemd":
		name, absent = "hyperfilelens-agent.service", "hyperfilelens-agent.service.absent"
	case "launchd":
		name, absent = "launchd.plist", "launchd.plist.absent"
	case "windows-service":
		name, absent = "service.json", "service.absent"
	case "windows-task":
		name, absent = "task.xml", "task.absent"
	case "none":
		return nil
	default:
		return fmt.Errorf("unknown rollback service type")
	}
	present, err := copyItem(filepath.Join("service", name), maxRollbackSmallFile, 0644, true)
	if err != nil {
		return err
	}
	missing, err := copyItem(filepath.Join("service", absent), maxRollbackSmallFile, 0600, true)
	if err != nil {
		return err
	}
	if present == missing || (active && !present) {
		return fmt.Errorf("rollback service definition is missing or ambiguous")
	}
	if service == "systemd" {
		if _, err := copyItem("service/20-gateway-resources.conf", maxRollbackSmallFile, 0644, true); err != nil {
			return err
		}
	}
	return nil
}

func stageRollbackArchive(ctx context.Context, stage string, archive *os.File, name string) error {
	seen := map[string]bool{}
	add := func(name string, dir, regular bool, size int64, reader io.Reader) error {
		name = strings.TrimPrefix(strings.ReplaceAll(name, "\\", "/"), "./")
		if dir {
			switch strings.TrimSuffix(name, "/") {
			case ".", "", "config", "data":
				return nil
			default:
				return fmt.Errorf("unexpected rollback archive directory")
			}
		}
		limit := int64(maxRollbackSmallFile)
		switch name {
		case "config/agent.env", "config/config.json":
		case "data/agent.db":
			limit = maxRollbackDatabase
		default:
			return fmt.Errorf("unexpected rollback archive entry")
		}
		if !regular || seen[name] || size <= 0 || size > limit {
			return fmt.Errorf("invalid or duplicate rollback archive entry: %s", name)
		}
		seen[name] = true
		return copyRollbackFile(ctx, stage, name, reader, limit, 0600, size)
	}
	if name == "latest.zip" {
		info, err := archive.Stat()
		if err != nil {
			return err
		}
		zr, err := zip.NewReader(archive, info.Size())
		if err != nil {
			return err
		}
		for _, entry := range zr.File {
			f, err := entry.Open()
			if err != nil {
				return err
			}
			err = add(entry.Name, entry.FileInfo().IsDir(), entry.Mode().IsRegular(), int64(entry.UncompressedSize64), f)
			f.Close()
			if err != nil {
				return err
			}
		}
	} else {
		gz, err := gzip.NewReader(rollbackContextReader{ctx, archive})
		if err != nil {
			return err
		}
		defer gz.Close()
		verified := rollbackContextReader{ctx, gz}
		tr := tar.NewReader(verified)
		for {
			h, err := tr.Next()
			if err == io.EOF {
				break
			}
			if err != nil {
				return err
			}
			if err := add(h.Name, h.Typeflag == tar.TypeDir, h.Typeflag == tar.TypeReg, h.Size, tr); err != nil {
				return err
			}
		}
		// Read through the gzip checksum, bounding any tar trailer padding.
		if n, err := io.Copy(io.Discard, io.LimitReader(verified, (1<<20)+1)); err != nil || n > 1<<20 {
			return fmt.Errorf("rollback archive trailer failed verification")
		}
	}
	if !seen["config/agent.env"] || !seen["data/agent.db"] {
		return fmt.Errorf("rollback snapshot requires both agent.env and agent.db")
	}
	return nil
}

func copyRollbackFile(ctx context.Context, stage, name string, reader io.Reader, limit int64, mode fs.FileMode, expectedBytes int64) error {
	path, err := lifecycle.Path(stage, filepath.FromSlash(name))
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, mode)
	if err != nil {
		return err
	}
	n, err := io.Copy(f, io.LimitReader(rollbackContextReader{ctx, reader}, limit+1))
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if n > limit || (expectedBytes >= 0 && n != expectedBytes) {
		return fmt.Errorf("rollback file has unexpected size: %s", name)
	}
	return nil
}
