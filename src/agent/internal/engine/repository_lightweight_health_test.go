package engine

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	nassvc "hyperfilelens/agent/internal/service/nas"
)

func TestLightweightHealthReadsMetadataWithoutKopiaOrAdoption(t *testing.T) {
	base := t.TempDir()
	repoPath := filepath.Join(base, "hfl-repo-71")
	markerPath := filepath.Join(repoPath, repositoryOwnershipMarkerPath)
	if err := os.MkdirAll(filepath.Dir(markerPath), 0700); err != nil {
		t.Fatal(err)
	}
	owner := repositoryOwnership{DeploymentUUID: "deployment", RepositoryUUID: "repository", LocationDigest: "location", FormatVersion: 1, Signature: "signature", MarkerPath: repositoryOwnershipMarkerPath}
	raw, _ := json.Marshal(owner)
	if err := os.WriteFile(markerPath, raw, 0600); err != nil {
		t.Fatal(err)
	}
	// Use the actual Kopia filesystem layout, not the S3 logical object name.
	formatPath := filepath.Join(repoPath, "kopia.repository.f")
	if err := os.WriteFile(formatPath, []byte(`{"uniqueID":"id","keyAlgo":"algo"}`), 0600); err != nil {
		t.Fatal(err)
	}
	payload := Payload{Extra: map[string]any{
		"health_only": true, "health_check_mode": "lightweight", "health_timeout_seconds": 60,
		"allow_ownership_adoption": true,
		"repository":               map[string]any{"id": 71, "type": "proxy_fs", "path": repoPath, "base_path": base, "layout": "managed_subdir_v1", "ownership": map[string]any{"deployment_uuid": owner.DeploymentUUID, "repository_uuid": owner.RepositoryUUID, "location_digest": owner.LocationDigest, "format_version": 1, "signature": owner.Signature, "marker_path": owner.MarkerPath}},
	}}
	// A zero Engine has no Kopia configuration: success proves no CLI preparation.
	e := &Engine{}
	status, result, message := e.runManagedRepositoryStatus(context.Background(), ReporterSink{}, "health", payload)
	if status != "success" || result["ownership_verified"] != true {
		t.Fatalf("status=%s result=%v message=%s", status, result, message)
	}
	// Nested metadata is not scanned by a two-file observation.
	nested := filepath.Join(repoPath, "nested", repositoryOwnershipMarkerPath)
	if err := os.MkdirAll(filepath.Dir(nested), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(nested, []byte("unrelated"), 0600); err != nil {
		t.Fatal(err)
	}
	status, _, message = e.runManagedRepositoryStatus(context.Background(), ReporterSink{}, "health", payload)
	if status != "success" {
		t.Fatalf("recursive scan occurred: %s", message)
	}
	// A logical-name decoy must not satisfy the filesystem format check.
	if err := os.Rename(formatPath, filepath.Join(repoPath, "kopia.repository")); err != nil {
		t.Fatal(err)
	}
	status, _, _ = e.runManagedRepositoryStatus(context.Background(), ReporterSink{}, "health", payload)
	if status != "failed" {
		t.Fatal("logical object name accepted instead of filesystem blob filename")
	}
	if err := os.Rename(filepath.Join(repoPath, "kopia.repository"), formatPath); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(markerPath); err != nil {
		t.Fatal(err)
	}
	status, result, _ = e.runManagedRepositoryStatus(context.Background(), ReporterSink{}, "health", payload)
	if status != "failed" || result["error_code"] != "REPOSITORY_OWNERSHIP_INVALID" {
		t.Fatalf("missing owner accepted: %s %v", status, result)
	}
	if _, err := os.Stat(markerPath); !os.IsNotExist(err) {
		t.Fatal("health check adopted ownership")
	}
	if err := os.WriteFile(formatPath, []byte(`{}`), 0600); err != nil {
		t.Fatal(err)
	}
	status, _, _ = e.runManagedRepositoryStatus(context.Background(), ReporterSink{}, "health", payload)
	if status != "failed" {
		t.Fatal("invalid format accepted")
	}
}

func TestHealthMetadataRejectsLargeAndSymlinkFiles(t *testing.T) {
	root := t.TempDir()
	f := filepath.Join(root, "large")
	if err := os.WriteFile(f, make([]byte, repositoryMetadataMaxBytes+1), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := readHealthMetadata(f); err == nil {
		t.Fatal("oversized metadata accepted")
	}
	link := filepath.Join(root, "link")
	if err := os.Symlink(f, link); err != nil {
		t.Skip(err)
	}
	if _, err := readHealthMetadata(link); err == nil {
		t.Fatal("symlink accepted")
	}
}

func TestLightweightNASUsesPhysicalFormatNameInRepositorySubdir(t *testing.T) {
	dataDir := t.TempDir()
	t.Setenv("HFL_DATA_DIR", dataDir)
	mount := filepath.Join(dataDir, "mounts", "repositories", "repo-34-node-29")
	spec := repositorySpec{
		Type: "nas", Subdir: "hp-repos/storage-34",
		TargetNAS: &nassvc.Spec{MountPoint: mount},
	}
	repoPath, allowedRoot, err := filesystemRepositoryOwnershipPath(spec)
	if err != nil {
		t.Fatal(err)
	}
	if repoPath != filepath.Join(mount, "hp-repos", "storage-34") || allowedRoot != mount {
		t.Fatalf("unexpected NAS location: %s, %s", repoPath, allowedRoot)
	}
	if err := os.MkdirAll(repoPath, 0700); err != nil {
		t.Fatal(err)
	}
	format := []byte(`{"uniqueID":"id","keyAlgo":"algo"}`)
	if err := os.WriteFile(filepath.Join(repoPath, "kopia.repository.f"), format, 0600); err != nil {
		t.Fatal(err)
	}
	handle, err := os.OpenRoot(repoPath)
	if err != nil {
		t.Fatal(err)
	}
	defer handle.Close()
	raw, err := readHealthMetadataAt(handle, filesystemRepositoryFormatFile)
	if err != nil || string(raw) != string(format) {
		t.Fatalf("NAS physical format read failed: %s, %v", raw, err)
	}
}
