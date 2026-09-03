package engine

import (
	"archive/zip"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/kopia"
	"hyperfilelens/agent/internal/platform/process"
	"hyperfilelens/agent/internal/platform/vfs"
	nassvc "hyperfilelens/agent/internal/service/nas"
)

type staticConfigProvider struct {
	cfg *model.AgentConfig
}

func (p staticConfigProvider) Current() *model.AgentConfig {
	return p.cfg
}

func testRepositoryMountPoint(t *testing.T, repositoryID int64) string {
	t.Helper()
	return vfs.RepositoryMountPoint(vfs.DefaultAgentDataDir(), repositoryID, 0)
}

func TestParseNASRepositorySpec(t *testing.T) {
	spec, ok, err := parseRepositorySpec(map[string]any{
		"id":     42,
		"type":   "nas",
		"subdir": "hp-repos/storage-42",
		"nas": map[string]any{
			"protocol":    "nfs",
			"server":      "10.0.0.15",
			"export_path": "/volume1/backup",
			"mount_point": testRepositoryMountPoint(t, 42),
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if !ok || spec.Type != "nas" || spec.TargetNAS == nil {
		t.Fatalf("expected nas repository spec, got ok=%v spec=%#v", ok, spec)
	}
	if spec.TargetNAS.ExportPath != "/volume1/backup" {
		t.Fatalf("unexpected export path: %#v", spec.TargetNAS)
	}
}

func TestRepositoryNASPathRejectsEscapes(t *testing.T) {
	spec := repositorySpec{
		Type:   "nas",
		Subdir: "../outside",
		TargetNAS: mustNASSpec(t, map[string]any{
			"protocol":    "nfs",
			"server":      "10.0.0.15",
			"export_path": "/volume1/backup",
			"mount_point": testRepositoryMountPoint(t, 42),
		}),
	}
	if _, err := repositoryNASPath(spec); err == nil {
		t.Fatal("expected escaped subdir to be rejected")
	}
	spec.Subdir = "hp-repos/storage-42"
	got, err := repositoryNASPath(spec)
	if err != nil {
		t.Fatal(err)
	}
	want := filepath.Clean(testRepositoryMountPoint(t, 42) + "/hp-repos/storage-42")
	if got != want {
		t.Fatalf("expected %q, got %q", want, got)
	}
}

func TestNASRepositoryWriteDeniedClassifiesNASPermissionAndReadOnlyErrors(t *testing.T) {
	isClassified := func(spec nassvc.Spec, err error) bool {
		_, _, classified := classifyNASRepositoryWriteError(spec, err, "test")
		return classified
	}
	smb := mustNASSpec(t, map[string]any{
		"protocol": "smb", "server": "10.0.0.15", "share": "backup",
		"username": "backup", "password": "secret", "mount_point": testRepositoryMountPoint(t, 42),
	})
	if !isClassified(*smb, fs.ErrPermission) {
		t.Fatal("expected SMB permission error to be classified")
	}
	nfs := *smb
	nfs.Protocol = "nfs"
	if !isClassified(nfs, fs.ErrPermission) {
		t.Fatal("expected NFS permission error to be classified")
	}
	if !isClassified(nfs, fmt.Errorf("read-only file system")) {
		t.Fatal("expected read-only NFS error to be classified")
	}
	if isClassified(*smb, fs.ErrNotExist) {
		t.Fatal("did not expect non-permission error to be classified")
	}
}

func TestFilesystemRepositoryOwnershipMatchesInterruptedInitialization(t *testing.T) {
	basePath := t.TempDir()
	spec := repositorySpec{
		ID:       42,
		Type:     "proxy_fs",
		Path:     filepath.Join(basePath, "hfl-repo-42"),
		BasePath: basePath,
		Layout:   "managed_subdir_v1",
		Ownership: &repositoryOwnership{
			DeploymentUUID: "deployment-1",
			RepositoryUUID: "repository-42",
			LocationDigest: "location-42",
			MarkerPath:     repositoryOwnershipMarkerPath,
			FormatVersion:  1,
			Signature:      "signature-42",
		},
	}

	matches, err := filesystemRepositoryOwnershipMatches(spec)
	if err != nil || matches {
		t.Fatalf("unexpected ownership before claim: matches=%v err=%v", matches, err)
	}
	if err := claimFilesystemRepositoryOwnership(spec); err != nil {
		t.Fatalf("claim ownership: %v", err)
	}
	matches, err = filesystemRepositoryOwnershipMatches(spec)
	if err != nil || !matches {
		t.Fatalf("expected matching interrupted ownership: matches=%v err=%v", matches, err)
	}

	foreign := spec
	foreignOwnership := *spec.Ownership
	foreignOwnership.RepositoryUUID = "repository-foreign"
	foreign.Ownership = &foreignOwnership
	if _, err := filesystemRepositoryOwnershipMatches(foreign); err == nil {
		t.Fatal("expected a foreign ownership marker to be rejected")
	}
}

func TestClaimFilesystemRepositoryOwnershipRecoversOnlyKopiaProbeResidue(t *testing.T) {
	dataDir := t.TempDir()
	t.Setenv("HFL_DATA_DIR", dataDir)
	mountPoint := vfs.RepositoryMountPoint(dataDir, 42, 14)
	repositoryPath := filepath.Join(mountPoint, "hp-repos", "agent-14")
	if err := os.MkdirAll(repositoryPath, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(
		filepath.Join(repositoryPath, ".shards"),
		[]byte(`{"default":[3,3],"maxNonShardedLength":20}`),
		0o600,
	); err != nil {
		t.Fatal(err)
	}
	spec := repositorySpec{
		ID:     42,
		Type:   "nas",
		Subdir: "hp-repos/agent-14",
		TargetNAS: mustNASSpec(t, map[string]any{
			"protocol": "nfs", "server": "10.0.0.15",
			"export_path": "/volume1/backup", "mount_point": mountPoint,
		}),
		Ownership: &repositoryOwnership{
			DeploymentUUID: "deployment-1",
			RepositoryUUID: "repository-42",
			LocationDigest: "location-42",
			MarkerPath:     repositoryOwnershipMarkerPath,
			FormatVersion:  1,
			Signature:      "signature-42",
		},
	}

	if err := claimFilesystemRepositoryOwnership(spec); err != nil {
		t.Fatalf("claim ownership after safe probe residue: %v", err)
	}
	if _, err := os.Lstat(filepath.Join(repositoryPath, ".shards")); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("expected probe residue to be removed, err=%v", err)
	}
	if matches, err := filesystemRepositoryOwnershipMatches(spec); err != nil || !matches {
		t.Fatalf("expected ownership marker after recovery: matches=%v err=%v", matches, err)
	}
}

func TestClaimFilesystemRepositoryOwnershipRejectsUnknownRepositoryData(t *testing.T) {
	dataDir := t.TempDir()
	t.Setenv("HFL_DATA_DIR", dataDir)
	mountPoint := vfs.RepositoryMountPoint(dataDir, 42, 14)
	repositoryPath := filepath.Join(mountPoint, "hp-repos", "agent-14")
	if err := os.MkdirAll(repositoryPath, 0o755); err != nil {
		t.Fatal(err)
	}
	shardsPath := filepath.Join(repositoryPath, ".shards")
	if err := os.WriteFile(shardsPath, []byte(`{"default":[3,3],"maxNonShardedLength":20}`), 0o600); err != nil {
		t.Fatal(err)
	}
	unknownPath := filepath.Join(repositoryPath, "backup-data")
	if err := os.WriteFile(unknownPath, []byte("must be preserved"), 0o600); err != nil {
		t.Fatal(err)
	}
	spec := repositorySpec{
		ID:     42,
		Type:   "nas",
		Subdir: "hp-repos/agent-14",
		TargetNAS: mustNASSpec(t, map[string]any{
			"protocol": "nfs", "server": "10.0.0.15",
			"export_path": "/volume1/backup", "mount_point": mountPoint,
		}),
		Ownership: &repositoryOwnership{
			DeploymentUUID: "deployment-1",
			RepositoryUUID: "repository-42",
			LocationDigest: "location-42",
			MarkerPath:     repositoryOwnershipMarkerPath,
			FormatVersion:  1,
			Signature:      "signature-42",
		},
	}

	err := claimFilesystemRepositoryOwnership(spec)
	if !errors.Is(err, errRepositoryDirectoryContainsData) {
		t.Fatalf("expected unknown data rejection, got %v", err)
	}
	if _, err := os.Stat(shardsPath); err != nil {
		t.Fatalf("unknown repository data must be preserved: %v", err)
	}
	if _, err := os.Stat(unknownPath); err != nil {
		t.Fatalf("unknown repository data must be preserved: %v", err)
	}
}

func TestManagedRepositoryStatusDoesNotConnectWithoutOwnership(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("fake Kopia shell script is Unix-only")
	}
	tempDir := t.TempDir()
	basePath := filepath.Join(tempDir, "repositories")
	repositoryPath := filepath.Join(basePath, "hfl-repo-42")
	commandLog := filepath.Join(tempDir, "commands.log")
	kopiaPath := filepath.Join(tempDir, "kopia")
	script := fmt.Sprintf("#!/bin/sh\nprintf '%%s\\n' \"$*\" >> %q\nexit 0\n", commandLog)
	if err := os.WriteFile(kopiaPath, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir:   filepath.Join(tempDir, "data"),
		KopiaPath: kopiaPath,
	}})
	payload := ParsePayload(map[string]any{"repository": map[string]any{
		"id": 42, "type": "proxy_fs", "path": repositoryPath,
		"base_path": basePath, "layout": "managed_subdir_v1",
		"kopia_password": "repo-pass",
		"ownership": map[string]any{
			"deployment_uuid": "deployment-1", "repository_uuid": "repository-42",
			"location_digest": "location-42", "format_version": 1,
			"signature": "signature-42", "marker_path": repositoryOwnershipMarkerPath,
		},
	}})

	status, result, message := engine.runManagedRepositoryStatus(
		context.Background(), ReporterSink{}, "task-1", payload,
	)
	if status != "failed" || !strings.Contains(message, "ownership marker is missing") {
		t.Fatalf("unexpected status result=%q message=%q payload=%#v", status, message, result)
	}
	if result["error_code"] != "REPOSITORY_OWNERSHIP_INVALID" {
		t.Fatalf("unexpected error code: %#v", result)
	}
	commands, err := os.ReadFile(commandLog)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		t.Fatal(err)
	}
	if strings.Contains(string(commands), "repository connect") {
		t.Fatalf("status probe must not connect an unowned location: %q", commands)
	}
}

func TestManagedRepositoryStatusCanSkipForeignOwnershipForRestoreValidation(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("fake Kopia shell script is Unix-only")
	}
	tempDir := t.TempDir()
	kopiaPath := filepath.Join(tempDir, "kopia")
	commandLog := filepath.Join(tempDir, "commands.log")
	script := fmt.Sprintf("#!/bin/sh\nprintf '%%s\\n' \"$*\" >> %q\nexit 0\n", commandLog)
	if err := os.WriteFile(kopiaPath, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	basePath := filepath.Join(tempDir, "repositories")
	repositoryPath := filepath.Join(basePath, "hfl-repo-42")
	foreignSpec := repositorySpec{
		ID: 42, Type: "proxy_fs", Path: repositoryPath, BasePath: basePath, Layout: "managed_subdir_v1",
		Ownership: &repositoryOwnership{
			DeploymentUUID: "deployment-1", RepositoryUUID: "repository-foreign",
			LocationDigest: "location-42", FormatVersion: 1,
			Signature: "signature-42", MarkerPath: repositoryOwnershipMarkerPath,
		},
	}
	if err := claimFilesystemRepositoryOwnership(foreignSpec); err != nil {
		t.Fatalf("create foreign ownership marker: %v", err)
	}
	repository := map[string]any{
		"id": 42, "type": "proxy_fs", "path": repositoryPath,
		"base_path": basePath, "layout": "managed_subdir_v1",
		"kopia_password": "repo-pass",
		"ownership": map[string]any{
			"deployment_uuid": "deployment-1", "repository_uuid": "repository-current",
			"location_digest": "location-42", "format_version": 1,
			"signature": "signature-42", "marker_path": repositoryOwnershipMarkerPath,
		},
	}
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir: filepath.Join(tempDir, "data"), KopiaPath: kopiaPath,
	}})

	status, _, message := engine.runManagedRepositoryStatus(
		context.Background(), ReporterSink{}, "task-default", ParsePayload(map[string]any{"repository": repository}),
	)
	if status != "failed" || !strings.Contains(message, "ownership") {
		t.Fatalf("expected foreign ownership failure, status=%q message=%q", status, message)
	}

	status, result, message := engine.runManagedRepositoryStatus(
		context.Background(), ReporterSink{}, "task-restore-validation", ParsePayload(map[string]any{
			"probe": "restore_target_validation", "skip_ownership_check": true, "repository": repository,
		}),
	)
	if status != "success" || message != "" {
		t.Fatalf("skip ownership validation failed: status=%q message=%q result=%#v", status, message, result)
	}
	if result["ownership_verified"] == true {
		t.Fatalf("restore validation must not report ownership as verified: %#v", result)
	}
}

func TestRepositoryArgsDisableCredentialPersistence(t *testing.T) {
	spec := repositorySpec{
		Type:   "s3",
		Bucket: "backup-bucket",
		Prefix: "org/repo",
		Region: "us-east-1",
	}

	for _, create := range []bool{true, false} {
		args := repositoryArgs("/tmp/repo.config", spec, create)
		if !slices.Contains(args, "--no-persist-credentials") {
			t.Fatalf("expected --no-persist-credentials in args: %#v", args)
		}
	}
}

func TestParseS3RepositoryURLStyle(t *testing.T) {
	tests := []struct {
		name      string
		value     any
		expected  string
		wantError bool
	}{
		{name: "missing defaults to auto", expected: "auto"},
		{name: "virtual hosted", value: "virtual_hosted", expected: "virtual_hosted"},
		{name: "path", value: "path", expected: "path"},
		{name: "invalid", value: "dns", wantError: true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			spec, ok, err := parseRepositorySpec(map[string]any{
				"type":         "s3",
				"bucket":       "backup-bucket",
				"s3_url_style": tc.value,
			})
			if tc.wantError {
				if err == nil {
					t.Fatal("expected invalid URL style to fail")
				}
				return
			}
			if err != nil || !ok || spec.S3URLStyle != tc.expected {
				t.Fatalf("expected URL style %q, got ok=%v spec=%#v err=%v", tc.expected, ok, spec, err)
			}
		})
	}
}

func TestRepositoryArgsIncludePatchedS3URLStyle(t *testing.T) {
	spec := repositorySpec{
		Type:           "s3",
		Bucket:         "backup-bucket",
		S3URLStyle:     "virtual_hosted",
		S3URLStyleFlag: true,
	}
	args := repositoryArgs("/tmp/repo.config", spec, false)
	if !slices.Contains(args, "--url-style=virtual-hosted") {
		t.Fatalf("expected virtual-hosted URL style in args: %#v", args)
	}
	spec.S3URLStyle = "path"
	args = repositoryArgs("/tmp/repo.config", spec, true)
	if !slices.Contains(args, "--url-style=path") {
		t.Fatalf("expected path URL style in args: %#v", args)
	}
}

func TestResolveKopiaS3URLStyleCapabilityCachesByBinaryIdentity(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("fake Kopia shell script is Unix-only")
	}
	tempDir := t.TempDir()
	commandLog := filepath.Join(tempDir, "commands.log")
	kopiaPath := filepath.Join(tempDir, "kopia")
	script := fmt.Sprintf(
		"#!/bin/sh\nprintf '%%s\\n' \"$*\" >> %q\necho '--url-style string'\nexit 0\n",
		commandLog,
	)
	if err := os.WriteFile(kopiaPath, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	for range 2 {
		spec := repositorySpec{Type: "s3", S3URLStyle: "virtual_hosted"}
		if err := resolveKopiaS3URLStyleCapability(context.Background(), kopiaPath, &spec); err != nil {
			t.Fatal(err)
		}
		if !spec.S3URLStyleFlag {
			t.Fatal("expected cached capability to set S3 URL style flag")
		}
	}
	raw, err := os.ReadFile(commandLog)
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Count(string(raw), "repository create s3 --help"); got != 1 {
		t.Fatalf("capability probe ran %d times, want 1: %q", got, string(raw))
	}
}

func TestS3ConnectionFingerprintChangesWithURLStyleAndCredentials(t *testing.T) {
	spec := repositorySpec{
		Type:            "s3",
		Bucket:          "backup-bucket",
		AccessKeyID:     "access",
		SecretAccessKey: "secret-one",
		S3URLStyle:      "auto",
	}
	first, err := s3ConnectionFingerprint(spec)
	if err != nil {
		t.Fatal(err)
	}
	spec.S3URLStyle = "virtual_hosted"
	second, err := s3ConnectionFingerprint(spec)
	if err != nil {
		t.Fatal(err)
	}
	if first == second {
		t.Fatal("URL style must change the connection fingerprint")
	}
	spec.SecretAccessKey = "secret-two"
	third, err := s3ConnectionFingerprint(spec)
	if err != nil {
		t.Fatal(err)
	}
	if second == third {
		t.Fatal("credentials must change the connection fingerprint")
	}
}

func TestRepositoryCreateAlreadyExists(t *testing.T) {
	for _, output := range []string{
		"repository already exists in storage",
		"Repository Already Initialized",
		"Kopia repository exists",
		"unable to get repository storage: found existing data in storage location",
	} {
		if !repositoryCreateAlreadyExists(process.Result{Stderr: output}) {
			t.Fatalf("expected existing repository output to be detected: %q", output)
		}
	}
	if repositoryCreateAlreadyExists(process.Result{Stderr: "access denied"}) {
		t.Fatal("unexpected existing repository detection for unrelated error")
	}
}

func TestRepositoryCommandFailureMessage(t *testing.T) {
	if got := repositoryCommandFailureMessage(process.Result{Stderr: "access denied"}, fmt.Errorf("exit 1"), repositorySpec{}, Payload{}); got != "access denied" {
		t.Fatalf("expected stderr reason, got %q", got)
	}
	if got := repositoryCommandFailureMessage(process.Result{Stdout: "repository unavailable"}, fmt.Errorf("exit 1"), repositorySpec{}, Payload{}); got != "repository unavailable" {
		t.Fatalf("expected stdout reason, got %q", got)
	}
	if got := repositoryCommandFailureMessage(process.Result{}, fmt.Errorf("exit 1"), repositorySpec{}, Payload{}); got != "exit 1" {
		t.Fatalf("expected fallback error, got %q", got)
	}
	spec := repositorySpec{KopiaPassword: "repo-secret"}
	if got := repositoryCommandFailureMessage(process.Result{Stderr: "failed for repo-secret"}, nil, spec, Payload{}); got != "failed for <redacted>" {
		t.Fatalf("expected repository command output to be redacted, got %q", got)
	}
	redacted := redactedRepositoryCommandResult(
		process.Result{Stderr: "failed for repo-secret"},
		spec,
		Payload{},
	)
	if got := redacted["stderr_tail"]; got != "failed for <redacted>" {
		t.Fatalf("expected repository result output to be redacted, got %#v", got)
	}
}

func TestManagedRestoreFailureMessage(t *testing.T) {
	spec := repositorySpec{
		SecretAccessKey: "s3-secret",
		KopiaPassword:   "repo-secret",
		ServerPassword:  "server-secret",
		TargetNAS: &nassvc.Spec{
			Password: "nas-secret",
		},
	}
	got := managedRestoreFailureMessage(
		process.Result{
			Stdout: "stdout fallback",
			Stderr: "permission denied for repo-secret, s3-secret, nas-secret, and target-secret",
		},
		fmt.Errorf("exit status 1"),
		spec,
		Payload{Extra: map[string]any{"nas": map[string]any{
			"protocol":    "smb",
			"server":      "nas.example.test",
			"share":       "restore",
			"mount_point": "/mnt/restore",
			"username":    "restore-user",
			"password":    "target-secret",
		}}},
	)
	if got != "Restore failed: permission denied for <redacted>, <redacted>, <redacted>, and <redacted>" {
		t.Fatalf("expected sanitized stderr reason, got %q", got)
	}
	if fallback := managedRestoreFailureMessage(
		process.Result{},
		fmt.Errorf("exit status 1"),
		repositorySpec{},
		Payload{},
	); fallback != "Restore failed: exit status 1" {
		t.Fatalf("expected process fallback, got %q", fallback)
	}
	result := managedRestoreCommandResult(
		process.Result{
			Stdout: "repository repo-secret",
			Stderr: "credentials s3-secret server-secret",
		},
		spec,
		Payload{},
	)
	for _, key := range []string{"stdout", "stderr", "stdout_tail", "stderr_tail"} {
		value, _ := result[key].(string)
		if strings.Contains(value, "secret") {
			t.Fatalf("expected %s to be sanitized, got %q", key, value)
		}
	}
}

func TestManagedRestorePreparationFailureSanitizesNestedResult(t *testing.T) {
	payload := Payload{
		Env: map[string]string{"KOPIA_PASSWORD": "env-secret"},
		Extra: map[string]any{
			"repository": map[string]any{
				"password": "payload-secret",
				"credentials": map[string]any{
					"access_token": map[string]any{"value": "nested-token"},
				},
			},
		},
	}
	result := map[string]any{
		"repository_connect": map[string]any{
			"stderr":        "connect rejected payload-secret, env-secret, and nested-token",
			"access_token":  "opaque-value-not-mentioned-in-payload",
			"numeric_token": 123456,
		},
		"attempts": []map[string]any{
			{
				"message":      "retry rejected list-secret",
				"access_token": "list-secret",
			},
		},
	}
	payload.Extra["attempts"] = []map[string]any{
		{"access_token": "list-secret"},
	}
	message := managedRestorePreparationFailureMessage(
		result,
		"exit status 1",
		repositorySpec{},
		payload,
	)
	if message != "Restore repository preparation failed: connect rejected <redacted>, <redacted>, and <redacted>" {
		t.Fatalf("expected actionable sanitized preparation error, got %q", message)
	}
	redacted := managedRestoreResult(result, repositorySpec{}, payload)
	connect := redacted["repository_connect"].(map[string]any)
	if got := connect["stderr"]; got != "connect rejected <redacted>, <redacted>, and <redacted>" {
		t.Fatalf("expected nested command result to be sanitized, got %q", got)
	}
	if got := connect["access_token"]; got != "<redacted>" {
		t.Fatalf("expected secret-shaped result field to be sanitized, got %q", got)
	}
	if got := connect["numeric_token"]; got != "<redacted>" {
		t.Fatalf("expected numeric secret-shaped result field to be sanitized, got %q", got)
	}
	attempts := redacted["attempts"].([]map[string]any)
	if got := attempts[0]["message"]; got != "retry rejected <redacted>" {
		t.Fatalf("expected typed map slice message to be sanitized, got %q", got)
	}
	if got := attempts[0]["access_token"]; got != "<redacted>" {
		t.Fatalf("expected typed map slice secret field to be sanitized, got %q", got)
	}
}

func TestSnapshotRestoreInspectFailureMessageSanitizesOutput(t *testing.T) {
	got := snapshotRestoreInspectFailureMessage(
		process.Result{Stderr: "inspect failed with repo-secret"},
		fmt.Errorf("exit status 1"),
		repositorySpec{KopiaPassword: "repo-secret"},
		Payload{},
	)
	if got != "Snapshot restore inspect failed: inspect failed with <redacted>" {
		t.Fatalf("expected sanitized inspect failure, got %q", got)
	}
}

func TestRedactManagedRestoreSecretsReplacesOverlappingValuesLongestFirst(t *testing.T) {
	got := redactManagedRestoreSecrets(
		"credentials abcdef and abc",
		repositorySpec{
			SecretAccessKey: "abc",
			KopiaPassword:   "abcdef",
			ServerPassword:  "abc",
		},
		Payload{},
	)
	if got != "credentials <redacted> and <redacted>" {
		t.Fatalf("expected overlapping secrets to be fully redacted, got %q", got)
	}
}

func TestManagedRestoreFailureMessageKeepsBoundedTail(t *testing.T) {
	prefix := strings.Repeat("old context ", 300)
	got := managedRestoreFailureMessage(
		process.Result{Stderr: prefix + "actionable final reason"},
		fmt.Errorf("exit status 1"),
		repositorySpec{},
		Payload{},
	)
	if len(got) > 2000 {
		t.Fatalf("expected bounded message, got %d bytes", len(got))
	}
	if !strings.HasSuffix(got, "actionable final reason") {
		t.Fatalf("expected actionable tail, got %q", got)
	}
	if !utf8.ValidString(got) {
		t.Fatalf("expected valid UTF-8, got %q", got)
	}

	multibyteContext := string([]rune{utf8.RuneSelf, 1 << 11, 1 << 16})
	multibyteFinalReason := string([]rune{utf8.RuneSelf + 1, 1<<11 + 1, 1<<16 + 1})
	got = managedRestoreFailureMessage(
		process.Result{Stderr: strings.Repeat(multibyteContext, 1000) + multibyteFinalReason},
		fmt.Errorf("exit status 1"),
		repositorySpec{},
		Payload{},
	)
	if len(got) > 2000 || !utf8.ValidString(got) || !strings.HasSuffix(got, multibyteFinalReason) {
		t.Fatalf("expected bounded valid UTF-8 tail, got bytes=%d value=%q", len(got), got)
	}
}

func TestManagedRepositoryInitializeRejectsExistingWithoutConnect(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("fake Kopia shell script is Unix-only")
	}
	tempDir := t.TempDir()
	commandLog := filepath.Join(tempDir, "commands.log")
	kopiaPath := filepath.Join(tempDir, "kopia")
	script := fmt.Sprintf(
		"#!/bin/sh\nprintf '%%s\\n' \"$*\" >> %q\necho 'unable to get repository storage: found existing data in storage location' >&2\nexit 1\n",
		commandLog,
	)
	if err := os.WriteFile(kopiaPath, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir:   filepath.Join(tempDir, "data"),
		KopiaPath: kopiaPath,
	}})
	payload := ParsePayload(map[string]any{
		"repository": map[string]any{
			"id":             42,
			"type":           "proxy_fs",
			"path":           filepath.Join(tempDir, "repository"),
			"kopia_password": "repo-pass",
		},
	})

	status, result, message := engine.runManagedRepositoryInitialize(
		context.Background(),
		ReporterSink{},
		"task-1",
		payload,
	)

	if status != "failed" || message != repositoryAlreadyExistsMessage {
		t.Fatalf("unexpected result status=%q message=%q result=%#v", status, message, result)
	}
	if result["error_code"] != repositoryAlreadyExistsCode {
		t.Fatalf("unexpected error code: %#v", result)
	}
	commands, err := os.ReadFile(commandLog)
	if err != nil {
		t.Fatal(err)
	}
	commandText := string(commands)
	if !strings.Contains(commandText, "repository create filesystem") {
		t.Fatalf("expected create command, got %q", commandText)
	}
	if strings.Contains(commandText, "repository connect") {
		t.Fatalf("initialize must not connect an existing repository: %q", commandText)
	}
}

func TestManagedProxyFSInitializeResumesMatchingInterruptedOwnership(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("fake Kopia shell script is Unix-only")
	}
	tempDir := t.TempDir()
	basePath := filepath.Join(tempDir, "repositories")
	repositoryPath := filepath.Join(basePath, "hfl-repo-42")
	commandLog := filepath.Join(tempDir, "commands.log")
	kopiaPath := filepath.Join(tempDir, "kopia")
	script := fmt.Sprintf(
		"#!/bin/sh\nprintf '%%s\\n' \"$*\" >> %q\ncase \"$*\" in\n  *\"repository create filesystem\"*) echo 'repository already exists in storage' >&2; exit 1 ;;\nesac\nexit 0\n",
		commandLog,
	)
	if err := os.WriteFile(kopiaPath, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	repository := map[string]any{
		"id":             42,
		"type":           "proxy_fs",
		"path":           repositoryPath,
		"base_path":      basePath,
		"layout":         "managed_subdir_v1",
		"kopia_password": "repo-pass",
		"ownership": map[string]any{
			"deployment_uuid": "deployment-1",
			"repository_uuid": "repository-42",
			"location_digest": "location-42",
			"format_version":  1,
			"signature":       "signature-42",
			"marker_path":     repositoryOwnershipMarkerPath,
		},
	}
	spec, ok, err := parseRepositorySpec(repository)
	if err != nil || !ok {
		t.Fatalf("parse repository: ok=%v err=%v", ok, err)
	}
	if err := claimFilesystemRepositoryOwnership(spec); err != nil {
		t.Fatalf("claim interrupted ownership: %v", err)
	}
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir:   filepath.Join(tempDir, "data"),
		KopiaPath: kopiaPath,
	}})
	payload := ParsePayload(map[string]any{"repository": repository})

	status, result, message := engine.runManagedRepositoryInitialize(
		context.Background(),
		ReporterSink{},
		"task-1",
		payload,
	)

	if status != "success" || message != "" {
		t.Fatalf("unexpected result status=%q message=%q result=%#v", status, message, result)
	}
	if result["ownership_verified"] != true {
		t.Fatalf("ownership was not verified: %#v", result)
	}
	commands, err := os.ReadFile(commandLog)
	if err != nil {
		t.Fatal(err)
	}
	commandText := string(commands)
	if !strings.Contains(commandText, "repository create filesystem") ||
		!strings.Contains(commandText, "repository connect filesystem") ||
		!strings.Contains(commandText, "repository status") {
		t.Fatalf("interrupted initialization was not resumed: %q", commandText)
	}
}

func TestManagedRepositoriesUseConfigScopedKopiaCaches(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("fake Kopia shell script is Unix-only")
	}
	tempDir := t.TempDir()
	commandLog := filepath.Join(tempDir, "commands.log")
	kopiaPath := filepath.Join(tempDir, "kopia")
	script := fmt.Sprintf(
		"#!/bin/sh\nprintf '%%s|%%s\\n' \"$KOPIA_CACHE_DIRECTORY\" \"$*\" >> %q\nexit 0\n",
		commandLog,
	)
	if err := os.WriteFile(kopiaPath, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	cfg := &model.AgentConfig{DataDir: filepath.Join(tempDir, "data"), KopiaPath: kopiaPath}
	engine := New(staticConfigProvider{cfg: cfg})

	for _, repositoryID := range []int{41, 42} {
		payload := ParsePayload(map[string]any{
			"env": map[string]any{"KOPIA_CACHE_DIRECTORY": filepath.Join(tempDir, "shared-cache")},
			"repository": map[string]any{
				"id":             repositoryID,
				"type":           "proxy_fs",
				"path":           filepath.Join(tempDir, fmt.Sprintf("repository-%d", repositoryID)),
				"kopia_password": fmt.Sprintf("repo-pass-%d", repositoryID),
			},
		})
		status, result, message := engine.runManagedRepositoryInitialize(
			context.Background(), ReporterSink{}, fmt.Sprintf("task-%d", repositoryID), payload,
		)
		if status != "success" {
			t.Fatalf("repository %d initialize status=%q message=%q result=%#v", repositoryID, status, message, result)
		}
	}

	raw, err := os.ReadFile(commandLog)
	if err != nil {
		t.Fatal(err)
	}
	caches := map[string]struct{}{}
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		cacheDir, _, ok := strings.Cut(line, "|")
		if !ok || cacheDir == "" {
			t.Fatalf("unexpected command log line %q", line)
		}
		if cacheDir == filepath.Join(tempDir, "shared-cache") {
			t.Fatalf("managed repository honored unsafe shared cache override: %q", line)
		}
		caches[cacheDir] = struct{}{}
	}
	if len(caches) != 2 {
		t.Fatalf("managed repositories used %d cache directories, want 2: %#v", len(caches), caches)
	}
	for cacheDir := range caches {
		rel, err := filepath.Rel(managedRepositoryCacheRoot(cfg), cacheDir)
		if err != nil || rel == "." || strings.HasPrefix(rel, "..") {
			t.Fatalf("cache directory %q is outside managed root", cacheDir)
		}
	}
}

func TestManagedRepositoryStatusHealthOnlyControlsUsageMetrics(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("fake Kopia shell script is Unix-only")
	}
	tests := []struct {
		name             string
		healthOnly       any
		wantContentStats bool
	}{
		{name: "health only", healthOnly: true, wantContentStats: false},
		{name: "explicit false", healthOnly: false, wantContentStats: true},
		{name: "flag absent", wantContentStats: true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tempDir := t.TempDir()
			commandLog := filepath.Join(tempDir, "commands.log")
			kopiaPath := filepath.Join(tempDir, "kopia")
			script := fmt.Sprintf(
				"#!/bin/sh\nprintf '%%s\\n' \"$*\" >> %q\nexit 0\n",
				commandLog,
			)
			if err := os.WriteFile(kopiaPath, []byte(script), 0o700); err != nil {
				t.Fatal(err)
			}
			engine := New(staticConfigProvider{cfg: &model.AgentConfig{
				DataDir:   filepath.Join(tempDir, "data"),
				KopiaPath: kopiaPath,
			}})
			rawPayload := map[string]any{
				"repository": map[string]any{
					"id":             42,
					"type":           "proxy_fs",
					"path":           filepath.Join(tempDir, "repository"),
					"kopia_password": "repo-pass",
				},
			}
			if tt.healthOnly != nil {
				rawPayload["health_only"] = tt.healthOnly
			}

			status, result, message := engine.runManagedRepositoryStatus(
				context.Background(), ReporterSink{}, "task-1", ParsePayload(rawPayload),
			)

			if status != "success" {
				t.Fatalf("status=%q message=%q result=%#v", status, message, result)
			}
			rawCommands, err := os.ReadFile(commandLog)
			if err != nil {
				t.Fatal(err)
			}
			commands := string(rawCommands)
			if !strings.Contains(commands, "repository connect filesystem") {
				t.Fatalf("repository connect was not run: %q", commands)
			}
			if !strings.Contains(commands, "repository status") {
				t.Fatalf("repository status was not run: %q", commands)
			}
			if got := strings.Contains(commands, "content stats"); got != tt.wantContentStats {
				t.Fatalf("content stats present=%v, want %v: %q", got, tt.wantContentStats, commands)
			}
		})
	}
}

func TestManagedRepositoryRechecksRepeatedConnectAndStatus(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("fake Kopia shell script is Unix-only")
	}
	tempDir := t.TempDir()
	commandLog := filepath.Join(tempDir, "commands.log")
	connectedPath := filepath.Join(tempDir, "connected")
	kopiaPath := filepath.Join(tempDir, "kopia")
	script := fmt.Sprintf(
		"#!/bin/sh\nprintf '%%s\\n' \"$*\" >> %q\ncase \"$*\" in\n  *\"repository connect\"*) touch \"${1#--config-file=}\" %q; exit 0 ;;\n  *\"repository status\"*) if [ -f %q ]; then rm %q; exit 0; fi; exit 1 ;;\nesac\nexit 0\n",
		commandLog, connectedPath, connectedPath, connectedPath,
	)
	if err := os.WriteFile(kopiaPath, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir:   filepath.Join(tempDir, "data"),
		KopiaPath: kopiaPath,
	}})
	payload := ParsePayload(map[string]any{
		"health_only":          true,
		"backup_config_dir_id": 7,
		"repository": map[string]any{
			"id":             84,
			"type":           "proxy_fs",
			"path":           filepath.Join(tempDir, "repository"),
			"kopia_password": "repo-pass",
		},
	})

	for _, taskID := range []string{"task-1", "task-2"} {
		status, result, message := engine.runManagedRepositoryStatus(
			context.Background(), ReporterSink{}, taskID, payload,
		)
		if status != "success" {
			t.Fatalf("task=%s status=%q message=%q result=%#v", taskID, status, message, result)
		}
	}
	raw, err := os.ReadFile(commandLog)
	if err != nil {
		t.Fatal(err)
	}
	commands := string(raw)
	if got := strings.Count(commands, "repository connect filesystem"); got != 2 {
		t.Fatalf("connect ran %d times, want 2: %q", got, commands)
	}
	if got := strings.Count(commands, "repository status"); got != 3 {
		t.Fatalf("status ran %d times, want 3: %q", got, commands)
	}
}

func TestManagedRepositoryUsesStatusFirstForExistingConfig(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("fake Kopia shell script is Unix-only")
	}
	tempDir := t.TempDir()
	commandLog := filepath.Join(tempDir, "commands.log")
	kopiaPath := filepath.Join(tempDir, "kopia")
	script := fmt.Sprintf(
		"#!/bin/sh\nprintf '%%s\\n' \"$*\" >> %q\nexit 0\n",
		commandLog,
	)
	if err := os.WriteFile(kopiaPath, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		DataDir:   filepath.Join(tempDir, "data"),
		KopiaPath: kopiaPath,
	}})
	spec := repositorySpec{ID: 85, Type: "proxy_fs"}
	configFile := engine.repositoryConfigPath(spec)
	if err := os.MkdirAll(filepath.Dir(configFile), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(configFile, []byte("existing"), 0o600); err != nil {
		t.Fatal(err)
	}
	payload := ParsePayload(map[string]any{
		"health_only": true,
		"repository": map[string]any{
			"id":             85,
			"type":           "proxy_fs",
			"path":           filepath.Join(tempDir, "repository"),
			"kopia_password": "repo-pass",
		},
	})
	status, result, message := engine.runManagedRepositoryStatus(
		context.Background(), ReporterSink{}, "task-1", payload,
	)
	if status != "success" {
		t.Fatalf("status=%q message=%q result=%#v", status, message, result)
	}
	raw, err := os.ReadFile(commandLog)
	if err != nil {
		t.Fatal(err)
	}
	commands := string(raw)
	if strings.Contains(commands, "repository connect") {
		t.Fatalf("existing healthy config should not reconnect: %q", commands)
	}
	if got := strings.Count(commands, "repository status"); got != 1 {
		t.Fatalf("status ran %d times, want 1: %q", got, commands)
	}
}

func TestParseKopiaServerRepositorySpec(t *testing.T) {
	spec, ok, err := parseRepositorySpec(map[string]any{
		"id":                      42,
		"type":                    "kopia_server",
		"url":                     "https://10.0.0.65:51515",
		"username":                "hfl-backup",
		"password":                "server-pass",
		"server_cert_fingerprint": "ABC123",
		"kopia_password":          "repo-pass",
		"session_id":              "session-1",
	})
	if err != nil {
		t.Fatal(err)
	}
	if !ok || spec.Type != "kopia_server" {
		t.Fatalf("expected kopia_server spec, got ok=%v spec=%#v", ok, spec)
	}
	if spec.ServerURL != "https://10.0.0.65:51515" || spec.ServerUsername != "hfl-backup" {
		t.Fatalf("unexpected server spec: %#v", spec)
	}
}

func TestRepositoryArgsConnectKopiaServer(t *testing.T) {
	spec := repositorySpec{
		Type:           "kopia_server",
		ServerURL:      "https://10.0.0.65:51515",
		ServerCert:     "ABC123",
		ServerUsername: "hfl-backup-5976@hfl-proxy-74",
		ServerPassword: "server-pass",
	}

	args := repositoryArgs("/tmp/repo.config", spec, false)
	if !slices.Contains(args, "server") {
		t.Fatalf("expected server repository args, got %#v", args)
	}
	if !slices.Contains(args, "--url=https://10.0.0.65:51515") {
		t.Fatalf("expected server URL in args, got %#v", args)
	}
	if !slices.Contains(args, "--server-cert-fingerprint=ABC123") {
		t.Fatalf("expected server cert fingerprint in args, got %#v", args)
	}
	if !slices.Contains(args, "--override-username=hfl-backup-5976") {
		t.Fatalf("expected server user identity override in args, got %#v", args)
	}
	if !slices.Contains(args, "--override-hostname=hfl-proxy-74") {
		t.Fatalf("expected server host identity override in args, got %#v", args)
	}
}

func TestRepositoryPasswordEnvValueUsesServerPasswordForKopiaServer(t *testing.T) {
	spec := repositorySpec{
		Type:           "kopia_server",
		KopiaPassword:  "repo-pass",
		ServerPassword: "server-pass",
	}
	if got := repositoryPasswordEnvValue(spec); got != "server-pass" {
		t.Fatalf("expected server password for kopia_server connect, got %q", got)
	}

	spec.Type = "nas"
	if got := repositoryPasswordEnvValue(spec); got != "repo-pass" {
		t.Fatalf("expected repository password for direct repository, got %q", got)
	}
}

func TestRepositoryConfigPathSeparatesKopiaServerSessions(t *testing.T) {
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: t.TempDir()}})
	spec := repositorySpec{
		ID:        50,
		Type:      "kopia_server",
		ServerURL: "https://proxy.example.internal:51515",
		SessionID: "backup-task-1-repo-50",
	}

	got := engine.repositoryConfigPath(spec)
	if !strings.Contains(filepath.ToSlash(got), "/cache/repositories/") {
		t.Fatalf("repository config must stay under AgentRoot/cache/repositories, got %q", got)
	}
	if !strings.Contains(filepath.Base(got), "repo-50-server-backup-task-1-repo-50") {
		t.Fatalf("expected session-scoped kopia server config path, got %q", got)
	}

	spec.SessionID = "backup-task-2-repo-50"
	next := engine.repositoryConfigPath(spec)
	if next == got {
		t.Fatalf("expected different server sessions to use different config files: %q", got)
	}

	direct := engine.repositoryConfigPath(repositorySpec{ID: 50, Type: "nas", Subdir: "hp-repos/agent-22"})
	if !strings.HasPrefix(filepath.Base(direct), "repo-50-nas-") {
		t.Fatalf("expected NAS repository config path to include its namespace, got %q", direct)
	}

	otherShard := engine.repositoryConfigPath(repositorySpec{ID: 50, Type: "nas", Subdir: "hp-repos/agent-53"})
	if otherShard == direct {
		t.Fatalf("expected different NAS shards to use different config files: %q", direct)
	}

	backupMount := repositorySpec{
		ID: 50, Type: "nas", Subdir: "hp-repos/agent-22",
		TargetNAS: &nassvc.Spec{MountPoint: "/opt/hfl/mounts/repositories/repo-50-node-22"},
	}
	restoreMount := backupMount
	restoreNAS := *backupMount.TargetNAS
	restoreNAS.MountPoint = "/opt/hfl/mounts/restores/repo-50-node-22"
	restoreMount.TargetNAS = &restoreNAS
	if engine.repositoryConfigPath(backupMount) == engine.repositoryConfigPath(restoreMount) {
		t.Fatal("backup and temporary restore mounts must use different Kopia configs")
	}
}

func TestNormalizeKopiaServerUsernameRequiresUserAtHost(t *testing.T) {
	if got := normalizeKopiaServerUsername("HFL Backup"); got != "hfl-backup@localhost" {
		t.Fatalf("expected fallback host and lowercase user, got %q", got)
	}
	if got := normalizeKopiaServerUsername("User.Name@Proxy Host"); got != "user.name@proxy-host" {
		t.Fatalf("expected sanitized user@host, got %q", got)
	}
}

func TestRunWithTimeoutReturnsTimeout(t *testing.T) {
	err := runWithTimeout(context.Background(), 10*time.Millisecond, func() error {
		time.Sleep(time.Second)
		return nil
	})
	if err == nil {
		t.Fatal("expected timeout error")
	}
	if got := err.Error(); got != "operation timed out after 10ms" {
		t.Fatalf("unexpected error: %s", got)
	}
}

func TestRunProcessWithTimeoutReturnsTimeout(t *testing.T) {
	_, err := runProcessWithTimeout(context.Background(), 10*time.Millisecond, "/bin/sh", []string{"-c", "sleep 1"}, nil, "")
	if err == nil {
		t.Fatal("expected timeout error")
	}
	if !strings.Contains(err.Error(), "process timed out after 10ms") {
		t.Fatalf("unexpected error: %s", err)
	}
}

func mustNASSpec(t *testing.T, raw map[string]any) *nassvc.Spec {
	t.Helper()
	spec, err := nassvc.ParseSpec(raw)
	if err != nil {
		t.Fatal(err)
	}
	return &spec
}

func TestManagedBackupSnapshotArgsAvoidUnsupportedProgressIntervalFlag(t *testing.T) {
	args := managedBackupSnapshotArgs("/tmp/repo.config", "/tmp/source", "operation-123")

	if slices.Contains(args, "--progress-interval=3s") {
		t.Fatalf("snapshot args must not include unsupported --progress-interval flag: %#v", args)
	}
	if !slices.Contains(args, "--progress") {
		t.Fatalf("expected snapshot args to include --progress: %#v", args)
	}
	if !slices.Contains(args, "--progress-format=hfl-json") {
		t.Fatalf("expected snapshot args to request structured progress: %#v", args)
	}
	if !slices.Contains(args, "--json") {
		t.Fatalf("expected snapshot args to include --json: %#v", args)
	}
	if !slices.Contains(args, "--tags=hfl-operation:operation-123") {
		t.Fatalf("expected snapshot args to include operation tag: %#v", args)
	}
	if got := args[len(args)-3]; got != "/tmp/source" {
		t.Fatalf("expected source path before tag and --json, got %q in %#v", got, args)
	}
}

func TestManagedSnapshotStorageStatsArgsKeepCompleteChronologicalHistory(t *testing.T) {
	args := managedSnapshotStorageStatsArgs("/tmp/repo.config", "/tmp/source")

	for _, forbidden := range []string{"--reverse", "--max-results", "--tags"} {
		if slices.Contains(args, forbidden) {
			t.Fatalf("storage stats args must not include %s: %#v", forbidden, args)
		}
	}
	for _, required := range []string{"snapshot", "list", "--storage-stats", "--no-retention", "--json"} {
		if !slices.Contains(args, required) {
			t.Fatalf("storage stats args must include %s: %#v", required, args)
		}
	}
	if got := args[len(args)-1]; got != "/tmp/source" {
		t.Fatalf("expected exact source path last, got %q in %#v", got, args)
	}
}

func TestParseManagedSnapshotStorageStatsLineUsesRecoverableSummary(t *testing.T) {
	line := ` {"id":"snapshot-2","stats":{"totalSize":999,"fileCount":12,"dirCount":9},"rootEntry":{"summ":{"size":2519861074,"files":82897,"symlinks":3,"dirs":8130}},"storageStats":{"newData":{"objectBytes":294628341,"originalContentBytes":294631395,"packedContentBytes":96054274,"fileObjects":11119,"dirObjects":1788,"contents":12948}}},`

	metrics, ok := parseManagedSnapshotStorageStatsLine(line, "snapshot-2")
	if !ok {
		t.Fatal("expected matching storage statistics row")
	}
	want := map[string]int64{
		"recoverable_size_bytes":     2519861074,
		"size_bytes":                 2519861074,
		"file_count":                 82897,
		"dir_count":                  8130,
		"symlink_count":              3,
		"new_original_content_bytes": 294631395,
		"new_packed_content_bytes":   96054274,
	}
	for key, expected := range want {
		if got, _ := int64Value(metrics[key]); got != expected {
			t.Fatalf("%s=%d, want %d in %#v", key, got, expected, metrics)
		}
	}
	if _, ok := parseManagedSnapshotStorageStatsLine(line, "snapshot-other"); ok {
		t.Fatal("must ignore a non-matching snapshot id")
	}
}

func TestParseManagedSnapshotStorageStatsLinePreservesZeroAndRejectsIncompleteRows(t *testing.T) {
	zeroLine := `{"id":"snapshot-zero","rootEntry":{"summ":{"size":42,"files":2,"dirs":1}},"storageStats":{"newData":{"originalContentBytes":0,"packedContentBytes":0}}}`
	metrics, ok := parseManagedSnapshotStorageStatsLine(zeroLine, "snapshot-zero")
	if !ok {
		t.Fatal("expected a valid fully reused snapshot row")
	}
	if metrics["new_original_content_bytes"] != int64(0) || metrics["new_packed_content_bytes"] != int64(0) {
		t.Fatalf("zero storage statistics must be preserved: %#v", metrics)
	}

	incompleteLine := `{"id":"snapshot-incomplete","rootEntry":{"summ":{"size":42,"files":2,"dirs":1}},"storageStats":{"newData":{"originalContentBytes":21}}}`
	if _, ok := parseManagedSnapshotStorageStatsLine(incompleteLine, "snapshot-incomplete"); ok {
		t.Fatal("expected an incomplete storage statistics row to be rejected")
	}
}

func TestManagedBackupLegacySnapshotArgsRemoveStructuredProgress(t *testing.T) {
	args := managedBackupLegacySnapshotArgs("/tmp/repo.config", "/tmp/source")
	if slices.Contains(args, "--progress-format=hfl-json") {
		t.Fatalf("legacy args must omit structured progress: %#v", args)
	}
	if !slices.Contains(args, "--progress") || !slices.Contains(args, "--json") {
		t.Fatalf("legacy args must preserve progress and final JSON: %#v", args)
	}
}

func TestStructuredProgressUnsupportedRequiresMatchingUnknownFlag(t *testing.T) {
	if !managedSnapshotStructuredProgressUnsupported(process.Result{Stderr: "unknown long flag '--progress-format'"}) {
		t.Fatal("expected progress-format unknown flag to enable legacy fallback")
	}
	if managedSnapshotStructuredProgressUnsupported(process.Result{Stderr: "repository unavailable"}) {
		t.Fatal("unrelated failures must not enable legacy fallback")
	}
}

func TestKopiaCompletionPayloadPreservesLogicalCounters(t *testing.T) {
	reporter := &kopiaProgressReporter{
		hasSnapshot: true,
		lastSnapshot: kopia.ProgressSnapshot{
			SchemaVersion:  2,
			Phase:          "done",
			PercentKnown:   true,
			PercentValue:   100,
			ProcessedBytes: 4_130_621_386,
			UploadedBytes:  270_077_614,
			EstimatedBytes: 4_130_621_356,
			EstimatedKnown: true,
		},
	}
	payload := reporter.completionPayload("snapshot-3ec")
	if payload["bytes_done"] != int64(4_130_621_386) || payload["uploaded_bytes"] != int64(270_077_614) {
		t.Fatalf("completion reset byte domains: %#v", payload)
	}
	if payload["bytes_done"] == int64(1) || payload["bytes_total"] == int64(1) {
		t.Fatalf("completion must not use synthetic 1/1 counters: %#v", payload)
	}
	if payload["upload_speed_bps"] != int64(0) || payload["upload_speed_source"] != "completed" {
		t.Fatalf("completion must expire upload speed: %#v", payload)
	}
}

func TestParseSnapshotBrowseOutputIncludesDirectoriesAndFiles(t *testing.T) {
	stdout := `[
		{"name":"docs","path":"docs","type":"dir","is_dir":true,"size_bytes":0},
		{"name":"readme.txt","path":"docs/readme.txt","type":"file","is_dir":false,"size_bytes":12}
	]`

	rows := parseSnapshotBrowseOutput(stdout, "", "kopia-snapshot-1")
	if len(rows) != 2 {
		t.Fatalf("expected 2 entries, got %d", len(rows))
	}
	if rows[0]["type"] != "dir" || rows[0]["downloadable"] != true {
		t.Fatalf("expected first entry to be a downloadable dir, got %#v", rows[0])
	}
	if rows[1]["type"] != "file" || rows[1]["downloadable"] != true {
		t.Fatalf("expected second entry to be a downloadable file, got %#v", rows[1])
	}
	if rows[1]["path"] != "docs/readme.txt" {
		t.Fatalf("expected file path docs/readme.txt, got %#v", rows[1]["path"])
	}
}

func TestParseSnapshotBrowseOutputHandlesKopiaModeAndNestedPath(t *testing.T) {
	stdout := `[
		{"name":"images","type":"d","mode":"drwxr-xr-x","size":0},
		{"name":"logo.png","type":"f","mode":"-rw-r--r--","size":42}
	]`

	rows := parseSnapshotBrowseOutput(stdout, "docs", "kopia-snapshot-1")
	if len(rows) != 2 {
		t.Fatalf("expected 2 entries, got %d", len(rows))
	}
	if rows[0]["type"] != "dir" {
		t.Fatalf("expected type=d entry to be normalized to dir, got %#v", rows[0])
	}
	if rows[0]["path"] != "docs/images" {
		t.Fatalf("expected nested dir path docs/images, got %#v", rows[0]["path"])
	}
	if rows[1]["type"] != "file" {
		t.Fatalf("expected type=f entry to be normalized to file, got %#v", rows[1])
	}
	if rows[1]["path"] != "docs/logo.png" {
		t.Fatalf("expected nested file path docs/logo.png, got %#v", rows[1]["path"])
	}
}

func TestParseSnapshotBrowseOutputNormalizesCSTModifiedTimes(t *testing.T) {
	shanghai, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		t.Fatal(err)
	}
	previousLocal := time.Local
	time.Local = shanghai
	t.Cleanup(func() { time.Local = previousLocal })

	for name, stdout := range map[string]string{
		"json": `[{"name":"restore-check.txt","type":"file","modified_at":"2026-08-27 15:35:25 CST"}]`,
		"text": `-rw-r--r-- 76 2026-08-27 15:35:25 CST object-id restore-check.txt`,
	} {
		t.Run(name, func(t *testing.T) {
			rows := parseSnapshotBrowseOutput(stdout, "", "snapshot-1")
			if len(rows) != 1 {
				t.Fatalf("rows = %d, want 1", len(rows))
			}
			if got := rows[0]["modified_at"]; got != "2026-08-27T07:35:25Z" {
				t.Fatalf("modified_at = %v, want 2026-08-27T07:35:25Z", got)
			}
		})
	}
}

func TestFormatModTimeUTCPreservesInstants(t *testing.T) {
	shanghai, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct {
		name string
		raw  string
		want string
	}{
		{name: "RFC3339 UTC", raw: "2026-08-27T07:35:25Z", want: "2026-08-27T07:35:25Z"},
		{name: "RFC3339 offset", raw: "2026-08-27T15:35:25+08:00", want: "2026-08-27T07:35:25Z"},
		{name: "explicit UTC", raw: "2026-08-27 07:35:25 UTC", want: "2026-08-27T07:35:25Z"},
		{name: "local abbreviation", raw: "2026-08-27 15:35:25 CST", want: "2026-08-27T07:35:25Z"},
		{name: "timezone omitted", raw: "2026-08-27 15:35:25", want: "2026-08-27T07:35:25Z"},
		{name: "short local time", raw: "Aug 27 15:35", want: "2026-08-27T07:35:00Z"},
	} {
		t.Run(test.name, func(t *testing.T) {
			if got := formatModTimeUTCInLocation(test.raw, shanghai); got != test.want {
				t.Fatalf("formatModTimeUTCInLocation(%q) = %q, want %q", test.raw, got, test.want)
			}
		})
	}
	if got := formatModTimeUTCInLocation("2026-08-27 15:35:25 CST", time.UTC); got != "2026-08-27 15:35:25 CST" {
		t.Fatalf("ambiguous non-local zone must be preserved, got %q", got)
	}
}

func TestSnapshotScopeRecursiveLinesProduceTrustedTotals(t *testing.T) {
	stdout := `drwx------            5 2026-08-12 11:15:59 CST object-dir sub/
-rw-------            5 2026-08-12 11:15:59 CST object-file sub/b.txt
-rw-------            3 2026-08-12 11:15:59 CST object-root a.txt`

	var files int64
	var directories int64
	var sizeBytes int64
	for _, line := range strings.Split(stdout, "\n") {
		mode, size, _, _, ok := parseInsightSnapshotLongLine(line)
		if !ok {
			continue
		}
		if strings.HasPrefix(strings.ToLower(mode), "d") {
			directories++
			continue
		}
		files++
		sizeBytes += size
	}

	if files != 2 || directories != 1 || sizeBytes != 8 {
		t.Fatalf(
			"unexpected totals files=%d directories=%d size=%d",
			files,
			directories,
			sizeBytes,
		)
	}
}

func TestSnapshotScopeRecursiveTotalsSkipSpecialFiles(t *testing.T) {
	// Some snapshots contain symlinks, sockets, pipes or devices. These should
	// not cause the whole scope resolve to fail; they should simply be skipped.
	stdout := `drwx------            1 2026-08-12 11:15:59 CST object-dir sub/
Lrwxrwxrwx           12 2026-08-12 11:15:59 CST object-link sub/link
srwxrwxrwx            0 2026-08-12 11:15:59 CST object-sock sub/sock
-rw-------            7 2026-08-12 11:15:59 CST object-file sub/file.txt`

	var files int64
	var directories int64
	var sizeBytes int64
	var invalid bool
	for _, line := range strings.Split(stdout, "\n") {
		mode, size, _, _, ok := parseInsightSnapshotLongLine(line)
		if !ok {
			if strings.TrimSpace(line) != "" {
				invalid = true
			}
			continue
		}
		if strings.HasPrefix(strings.ToLower(mode), "d") {
			if size < 0 {
				invalid = true
				continue
			}
			directories++
			continue
		}
		if !strings.HasPrefix(strings.ToLower(mode), "-") {
			// Skip special file types without failing.
			continue
		}
		if size < 0 {
			invalid = true
			continue
		}
		files++
		if size > 0 && sizeBytes <= math.MaxInt64-size {
			sizeBytes += size
		} else if size > 0 {
			invalid = true
		}
	}

	if invalid {
		t.Fatal("expected special file types to be skipped, not marked invalid")
	}
	if files != 1 || directories != 1 || sizeBytes != 7 {
		t.Fatalf(
			"unexpected totals after skipping special files: files=%d directories=%d size=%d",
			files,
			directories,
			sizeBytes,
		)
	}
}

func TestInsightSnapshotBrowseCollectorSkipsSpecialEntries(t *testing.T) {
	collector := newInsightSnapshotBrowseCollector("docs", 10)
	lines := []string{
		"drwx------ 1 2026-08-12 11:15:59 CST object-dir sub/",
		"Lrwxrwxrwx 12 2026-08-12 11:15:59 CST object-link outside-link",
		"-rw------- 7 2026-08-12 11:15:59 CST object-file report.txt",
	}
	for _, line := range lines {
		if !collector.consume(line) {
			t.Fatalf("collector rejected valid entry %q", line)
		}
	}
	if collector.invalid {
		t.Fatal("special entry must not invalidate Insight browsing")
	}
	if collector.skippedSpecialCount != 1 {
		t.Fatalf("skipped special count = %d, want 1", collector.skippedSpecialCount)
	}
	if len(collector.entries) != 2 {
		t.Fatalf("entries = %d, want 2", len(collector.entries))
	}
}

func TestClassifyInsightSnapshotEntryRejectsInvalidSize(t *testing.T) {
	if _, valid := classifyInsightSnapshotEntry("-rw-------", -1); valid {
		t.Fatal("negative regular-file size must be rejected")
	}
	if kind, valid := classifyInsightSnapshotEntry("Lrwxrwxrwx", 12); !valid || kind != "special" {
		t.Fatalf("symlink classification = %q, %v; want special, true", kind, valid)
	}
}

func TestInsightSnapshotResultContainsOnlyBusinessIdentity(t *testing.T) {
	result := newInsightSnapshotResult(" snapshot-1 ", " /reports/quarterly/ ")

	if result["snapshot_id"] != "snapshot-1" || result["path"] != "reports/quarterly" {
		t.Fatalf("unexpected Insight result identity: %#v", result)
	}
	for _, key := range []string{
		"config_file",
		"repository_status",
		"repository_connect",
		"repository_create",
	} {
		if _, exists := result[key]; exists {
			t.Fatalf("Insight result must not contain %q: %#v", key, result)
		}
	}
}

func TestSnapshotScopeLongLineRejectsInvalidSize(t *testing.T) {
	line := "-rw------- not-a-size 2026-08-12 11:15:59 CST object-file report.txt"
	if _, _, _, _, ok := parseInsightSnapshotLongLine(line); ok {
		t.Fatal("expected invalid size to be rejected")
	}
	if _, size, _, _, ok := parseSnapshotBrowseLongLine(line); !ok || size != 0 {
		t.Fatal("shared snapshot browsing parser behavior must remain unchanged")
	}
}

func TestExactInt64ValueRejectsInvalidJSONNumbers(t *testing.T) {
	for _, value := range []any{1.5, math.NaN(), math.Inf(1), float64(math.MaxInt64)} {
		if _, ok := exactInt64Value(value); ok {
			t.Fatalf("expected invalid exact integer %v to be rejected", value)
		}
	}
	if value, ok := exactInt64Value(float64(42)); !ok || value != 42 {
		t.Fatalf("expected exact JSON integer, got value=%d ok=%v", value, ok)
	}
}

func TestSnapshotScopeSelectionIgnoresOtherEntries(t *testing.T) {
	selection := snapshotScopeSelection{name: "report.pdf"}
	selection.inspectLine(
		"-rw------- 12 2026-08-12 11:15:59 CST object-other other.pdf",
	)
	selection.inspectLine(
		"-rw------- 42 2026-08-12 11:15:59 CST object-report report.pdf",
	)

	if !selection.found || selection.invalidType {
		t.Fatalf("expected a valid selected file, got %#v", selection)
	}
	if selection.pathType != "file" || selection.sizeBytes != 42 {
		t.Fatalf("unexpected selected file summary: %#v", selection)
	}
	if selection.modifiedAt != formatModTimeUTC("2026-08-12 11:15:59 CST") {
		t.Fatalf("unexpected selected file timestamp: %#v", selection)
	}
}

func TestSnapshotScopeSelectionRejectsInvalidFileSize(t *testing.T) {
	selection := snapshotScopeSelection{name: "report.pdf"}
	selection.inspectLine(
		"-rw------- -1 2026-08-12 11:15:59 CST object-report report.pdf",
	)

	if !selection.found || !selection.invalidType {
		t.Fatalf("expected invalid selected file size, got %#v", selection)
	}
}

func TestSnapshotScopeSelectionRejectsUnsupportedEntryType(t *testing.T) {
	selection := snapshotScopeSelection{name: "latest"}
	selection.inspectLine(
		"lrwx------ 12 2026-08-12 11:15:59 CST object-link latest",
	)

	if !selection.found || !selection.invalidType {
		t.Fatalf("expected unsupported selected path type, got %#v", selection)
	}
}

func TestInsightSnapshotLongLinePreservesNegativeSizeForValidation(t *testing.T) {
	line := "drwx------ -1 2026-08-12 11:15:59 CST object-dir reports/"
	_, size, _, _, ok := parseInsightSnapshotLongLine(line)
	if !ok || size != -1 {
		t.Fatalf("expected a parsed negative size for fail-closed validation, got %d", size)
	}
}

func TestInsightSnapshotBrowseCollectorBoundsAndNormalizesEntries(t *testing.T) {
	collector := newInsightSnapshotBrowseCollector("reports", 2)
	collector.consume("drwx------ 0 2026-08-12 11:15:59 CST object-dir quarterly/")
	collector.consume("-rw------- 12 2026-08-12 11:15:59 CST object-one one.pdf")
	continueReading := collector.consume(
		"-rw------- 18 2026-08-12 11:15:59 CST object-two two.pdf",
	)

	if continueReading || len(collector.entries) != 2 || !collector.hasMore {
		t.Fatalf("expected two bounded entries and has_more, got %#v", collector)
	}
	if collector.entries[0]["path"] != "reports/quarterly" || collector.entries[0]["type"] != "dir" {
		t.Fatalf("unexpected directory entry: %#v", collector.entries[0])
	}
	if collector.entries[1]["path"] != "reports/one.pdf" || collector.entries[1]["size_bytes"] != int64(12) {
		t.Fatalf("unexpected file entry: %#v", collector.entries[1])
	}
}

func TestSnapshotBrowsePageCollectorAdvancesWithoutDuplicates(t *testing.T) {
	lines := []string{
		"drwx------ 0 2026-08-12 11:15:59 UTC object-dir reports/",
		"-rw------- 12 2026-08-12 11:15:59 UTC object-one one.pdf",
		"-rw------- 18 2026-08-12 11:15:59 UTC object-two two.pdf",
		"-rw------- 24 2026-08-12 11:15:59 UTC object-three three.pdf",
	}

	first, err := newSnapshotBrowsePageCollector("docs", 2, "")
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range lines {
		if !first.consume(line) {
			break
		}
	}
	if len(first.entries) != 2 || !first.hasMore || first.nextCursor() != "2" {
		t.Fatalf("unexpected first page: %#v", first)
	}

	second, err := newSnapshotBrowsePageCollector("docs", 2, first.nextCursor())
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range lines {
		if !second.consume(line) {
			break
		}
	}
	if len(second.entries) != 2 || second.hasMore || second.nextCursor() != "" {
		t.Fatalf("unexpected second page: %#v", second)
	}
	if first.entries[1]["path"] != "docs/one.pdf" || second.entries[0]["path"] != "docs/two.pdf" {
		t.Fatalf("pages overlap or skip entries: first=%#v second=%#v", first.entries, second.entries)
	}
}

func TestSnapshotBrowsePageCollectorRejectsInvalidCursor(t *testing.T) {
	for _, cursor := range []string{"-1", "next"} {
		if _, err := newSnapshotBrowsePageCollector("", 200, cursor); err == nil {
			t.Fatalf("expected cursor %q to be rejected", cursor)
		}
	}
}

func TestInsightSnapshotBrowseCollectorRejectsMalformedOutput(t *testing.T) {
	collector := newInsightSnapshotBrowseCollector("reports", 2)
	collector.consume("unexpected kopia output")
	collector.consume("lrwx------ 4 2026-08-12 11:15:59 CST object-link latest")

	if !collector.invalid || len(collector.entries) != 0 {
		t.Fatalf("expected malformed output to invalidate the result, got %#v", collector)
	}
}

func TestParseSnapshotBrowseLongLinePreservesNonBreakingSpacesInName(t *testing.T) {
	stdout := "-rw-rw-rw-      2489505 2023-11-06 17:40:51 CST Ix78b995bfccc4626e81396d891f72ff3d 2017\u00a0SX-020\u00a0Telecom MANO\u00a0Or-Vi Interface Requirements_20221017.pdf"

	rows := parseSnapshotBrowseOutput(stdout, "", "kopia-snapshot-1")
	if len(rows) != 1 {
		t.Fatalf("expected 1 entry, got %d", len(rows))
	}
	want := "2017\u00a0SX-020\u00a0Telecom MANO\u00a0Or-Vi Interface Requirements_20221017.pdf"
	if rows[0]["path"] != want {
		t.Fatalf("expected path %q, got %#v", want, rows[0]["path"])
	}
	if rows[0]["name"] != want {
		t.Fatalf("expected name %q, got %#v", want, rows[0]["name"])
	}
}

func TestParseSnapshotBrowseOutputHandlesKopiaLongTextOutput(t *testing.T) {
	stdout := `
drwxr-xr-x            3 2026-06-02 12:05:28 UTC k887b2e209fb3664e46bc285a8443f27e  images/
-rw-r--r--            5 2026-06-02 12:05:28 UTC 20f56748546ce9f44973ef79419115b0   readme.txt
`

	rows := parseSnapshotBrowseOutput(stdout, "docs", "kopia-snapshot-1")
	if len(rows) != 2 {
		t.Fatalf("expected 2 entries, got %d", len(rows))
	}
	if rows[0]["type"] != "dir" || rows[0]["path"] != "docs/images" {
		t.Fatalf("expected first long entry to be docs/images dir, got %#v", rows[0])
	}
	if rows[1]["type"] != "file" || rows[1]["path"] != "docs/readme.txt" {
		t.Fatalf("expected second long entry to be docs/readme.txt file, got %#v", rows[1])
	}
}

func TestRestoreTargetPathForFileSnapshotUsesFilenameUnderTargetDirectory(t *testing.T) {
	p := Payload{Extra: map[string]any{
		"source_path":      "/data/docs/readme.txt",
		"source_path_type": "file",
	}}

	got := restoreTargetPathForSelection(p, "/restore", "")
	want := filepath.Join("/restore", "readme.txt")
	if got != want {
		t.Fatalf("expected file restore target %q, got %q", want, got)
	}
}

func TestRestoreTargetPathWithFinalSemanticsUsesTargetPathAsIs(t *testing.T) {
	p := Payload{Extra: map[string]any{
		"source_path":           "/data/docs",
		"source_path_type":      "directory",
		"target_path_semantics": "final",
	}}

	got := restoreTargetPathForSelection(p, "/restore/docs-root_data_docs", "")
	want := "/restore/docs-root_data_docs"
	if got != want {
		t.Fatalf("expected final target path %q, got %q", want, got)
	}
}

func TestRestorePrepareTargetPathForFinalFileUsesParentDirectory(t *testing.T) {
	p := Payload{Extra: map[string]any{
		"source_path":           "/data/docs/readme.txt",
		"source_path_type":      "file",
		"target_path_semantics": "final",
	}}

	got := restorePrepareTargetPath(p, filepath.Join("/restore", "readme.txt"), []string{""})
	if got != "/restore" {
		t.Fatalf("expected final file restore to prepare parent directory, got %q", got)
	}
}

func TestRestorePrepareTargetPathForFinalSelectedPathUsesParentDirectory(t *testing.T) {
	p := Payload{Extra: map[string]any{
		"source_path":           "/data",
		"source_path_type":      "directory",
		"target_path_semantics": "final",
	}}

	got := restorePrepareTargetPath(p, filepath.Join("/restore", "readme.txt-data_docs_readme.txt"), []string{"docs/readme.txt"})
	if got != "/restore" {
		t.Fatalf("expected final selected-path restore to prepare parent directory, got %q", got)
	}
}

func TestRestorePrepareTargetPathForFinalSelectedFileUsesDetectedFileType(t *testing.T) {
	p := Payload{Extra: map[string]any{
		"source_path":           "/data/scripts",
		"source_path_type":      "directory",
		"target_path_semantics": "final",
	}}

	got := restorePrepareTargetPathForSelection(p, filepath.Join("/restore", "mariadb.sh"), 1, false)
	if got != "/restore" {
		t.Fatalf("expected final selected file restore to prepare parent directory, got %q", got)
	}
}

func TestRestorePrepareTargetPathForFinalSelectedFileWithoutExtensionUsesDetectedFileType(t *testing.T) {
	p := Payload{Extra: map[string]any{
		"source_path":           "/data/scripts",
		"source_path_type":      "directory",
		"target_path_semantics": "final",
	}}

	got := restorePrepareTargetPathForSelection(p, filepath.Join("/restore", "mariadb"), 1, false)
	if got != "/restore" {
		t.Fatalf("expected final selected extensionless file restore to prepare parent directory, got %q", got)
	}
}

func TestRestorePrepareTargetPathForFinalSelectedDirectoryUsesTargetDirectory(t *testing.T) {
	p := Payload{Extra: map[string]any{
		"source_path":           "/data",
		"source_path_type":      "directory",
		"target_path_semantics": "final",
	}}

	got := restorePrepareTargetPathForSelection(p, filepath.Join("/restore", "images"), 1, true)
	want := filepath.Join("/restore", "images")
	if got != want {
		t.Fatalf("expected final selected directory restore to prepare target directory %q, got %q", want, got)
	}
}

func TestRestorePrepareTargetPathForMultipleFinalSelectionsUsesTargetDirectory(t *testing.T) {
	p := Payload{Extra: map[string]any{
		"source_path":           "/data",
		"source_path_type":      "directory",
		"target_path_semantics": "final",
	}}

	got := restorePrepareTargetPathForSelection(p, "/restore/manual", 2, false)
	if got != "/restore/manual" {
		t.Fatalf("expected multiple selected path restore to prepare target directory, got %q", got)
	}
}

func TestRestorePrepareTargetPathForFinalDirectoryUsesTargetDirectory(t *testing.T) {
	p := Payload{Extra: map[string]any{
		"source_path":           "/data/docs",
		"source_path_type":      "directory",
		"target_path_semantics": "final",
	}}

	got := restorePrepareTargetPath(p, filepath.Join("/restore", "docs"), []string{""})
	want := filepath.Join("/restore", "docs")
	if got != want {
		t.Fatalf("expected final directory restore to prepare target directory %q, got %q", want, got)
	}
}

func TestPrepareRestoreTargetPathRemovesEmptyDirectoryForFileTarget(t *testing.T) {
	root := t.TempDir()
	target := filepath.Join(root, "mariadb.sh")
	if err := os.Mkdir(target, 0o755); err != nil {
		t.Fatal(err)
	}

	if err := prepareRestoreTargetPath(root, target, true, ""); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(target); !os.IsNotExist(err) {
		t.Fatalf("expected empty target directory to be removed, stat err=%v", err)
	}
}

func TestPrepareRestoreTargetPathRejectsNonEmptyDirectoryForFileTarget(t *testing.T) {
	root := t.TempDir()
	target := filepath.Join(root, "mariadb.sh")
	if err := os.Mkdir(target, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(target, "leftover"), []byte("data"), 0o644); err != nil {
		t.Fatal(err)
	}

	err := prepareRestoreTargetPath(root, target, true, "")
	if err == nil {
		t.Fatal("expected non-empty directory target to be rejected")
	}
	if !strings.Contains(err.Error(), "non-empty directory") {
		t.Fatalf("expected non-empty directory error, got %v", err)
	}
}

func TestRestoreTargetPathForDirectorySnapshotKeepsTargetDirectory(t *testing.T) {
	p := Payload{Extra: map[string]any{
		"source_path":      "/data/docs",
		"source_path_type": "directory",
	}}

	got := restoreTargetPathForSelection(p, "/restore", "")
	want := filepath.Join("/restore", "docs")
	if got != want {
		t.Fatalf("expected directory restore target %q, got %q", want, got)
	}
}

func TestRestoreTargetPathForSelectedDirectoryChildKeepsTargetDirectory(t *testing.T) {
	p := Payload{Extra: map[string]any{
		"source_path":      "/data/docs",
		"source_path_type": "directory",
	}}

	got := restoreTargetPathForSelection(p, "/restore", "images")
	if got != "/restore" {
		t.Fatalf("expected selected child restore target to stay /restore, got %q", got)
	}
}

func TestRestoreTargetPathForUnknownPathTypeKeepsTargetDirectory(t *testing.T) {
	p := Payload{Extra: map[string]any{
		"source_path":      "/data/docs",
		"source_path_type": "unknown",
	}}

	got := restoreTargetPathForSelection(p, "/restore", "")
	if got != "/restore" {
		t.Fatalf("expected unknown path type restore target to stay /restore, got %q", got)
	}
}

func TestParseSnapshotBrowseOutputExpandsKopiaDirectoryObject(t *testing.T) {
	stdout := `{
		"name":"docs",
		"type":"d",
		"entries":[
			{"name":"images","type":"d","mode":"drwxr-xr-x","size":0},
			{"name":"logo.png","type":"f","mode":"-rw-r--r--","size":42}
		]
	}`

	rows := parseSnapshotBrowseOutput(stdout, "docs", "kopia-snapshot-1")
	if len(rows) != 2 {
		t.Fatalf("expected 2 child entries, got %d: %#v", len(rows), rows)
	}
	if rows[0]["path"] != "docs/images" {
		t.Fatalf("expected nested dir path docs/images, got %#v", rows[0]["path"])
	}
	if rows[1]["path"] != "docs/logo.png" {
		t.Fatalf("expected nested file path docs/logo.png, got %#v", rows[1]["path"])
	}
}

func TestCollectRestoredDownloadReturnsZipForSingleFileDirectory(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "only.txt"), []byte("hello"), 0o644); err != nil {
		t.Fatal(err)
	}

	content, filename, contentType, err := collectRestoredDownload(root, "inner_dir1", true)
	if err != nil {
		t.Fatal(err)
	}
	if filename != "inner_dir1.zip" || contentType != "application/zip" {
		t.Fatalf("expected inner_dir1.zip application/zip, got %q %q", filename, contentType)
	}
	zr, err := zip.NewReader(bytes.NewReader(content), int64(len(content)))
	if err != nil {
		t.Fatalf("expected valid zip: %v", err)
	}
	if len(zr.File) != 1 || zr.File[0].Name != "only.txt" {
		t.Fatalf("expected zip to contain only.txt, got %#v", zr.File)
	}
}

func TestCollectRestoredDownloadReturnsFileForSingleFile(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "readme.txt"), []byte("hello"), 0o644); err != nil {
		t.Fatal(err)
	}

	content, filename, contentType, err := collectRestoredDownload(root, "readme.txt", false)
	if err != nil {
		t.Fatal(err)
	}
	if filename != "readme.txt" || contentType != "application/octet-stream" {
		t.Fatalf("expected readme.txt application/octet-stream, got %q %q", filename, contentType)
	}
	if string(content) != "hello" {
		t.Fatalf("unexpected file content %q", string(content))
	}
}

func TestCollectRestoredDownloadReturnsSingleDotfile(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, ".hidden-note"), []byte("hidden content"), 0o644); err != nil {
		t.Fatal(err)
	}

	content, filename, contentType, err := collectRestoredDownload(root, ".hidden-note", false)
	if err != nil {
		t.Fatal(err)
	}
	if filename != ".hidden-note" || contentType != "application/octet-stream" {
		t.Fatalf("expected .hidden-note application/octet-stream, got %q %q", filename, contentType)
	}
	if string(content) != "hidden content" {
		t.Fatalf("unexpected dotfile content %q", string(content))
	}
}

func TestCollectRestoredDownloadReturnsZipForDirectoryContainingOnlyDotfile(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, ".hidden-note"), []byte("hidden content"), 0o644); err != nil {
		t.Fatal(err)
	}

	content, filename, contentType, err := collectRestoredDownload(root, "hidden-dir", true)
	if err != nil {
		t.Fatal(err)
	}
	if filename != "hidden-dir.zip" || contentType != "application/zip" {
		t.Fatalf("expected hidden-dir.zip application/zip, got %q %q", filename, contentType)
	}
	zr, err := zip.NewReader(bytes.NewReader(content), int64(len(content)))
	if err != nil {
		t.Fatalf("expected valid zip: %v", err)
	}
	if len(zr.File) != 1 || zr.File[0].Name != ".hidden-note" {
		t.Fatalf("expected zip to contain .hidden-note, got %#v", zr.File)
	}
}

func TestUploadSnapshotArtifactStreamsFileWithIntegrityHeaders(t *testing.T) {
	t.Setenv("HFL_INSECURE_TLS", "0")
	content := []byte("streamed snapshot artifact")
	var received []byte
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPut || r.URL.Path != "/upload" {
			t.Fatalf("unexpected request %s %s", r.Method, r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer task-token" {
			t.Fatalf("unexpected authorization header")
		}
		if r.Header.Get("X-Content-SHA256") == "" || r.Header.Get("X-Artifact-Filename") != "report.txt" {
			t.Fatalf("missing artifact integrity headers: %#v", r.Header)
		}
		var err error
		received, err = io.ReadAll(r.Body)
		if err != nil {
			t.Fatal(err)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	filePath := filepath.Join(t.TempDir(), "report.txt")
	if err := os.WriteFile(filePath, content, 0o600); err != nil {
		t.Fatal(err)
	}
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{APIBaseURL: server.URL}})
	result, err := engine.uploadSnapshotArtifact(
		context.Background(),
		snapshotArtifactUploadSpec{ArtifactID: 7, Path: "/upload", Token: "task-token", MaxBytes: 1024},
		filePath,
		"report.txt",
		"application/octet-stream",
	)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(received, content) || result["artifact_id"] != int64(7) {
		t.Fatalf("unexpected upload result %#v content=%q", result, received)
	}
}

func TestUploadSnapshotArtifactHonorsInsecureTLS(t *testing.T) {
	content := []byte("self-signed snapshot artifact")
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPut || r.URL.Path != "/upload" {
			t.Fatalf("unexpected request %s %s", r.Method, r.URL.Path)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	filePath := filepath.Join(t.TempDir(), "report.txt")
	if err := os.WriteFile(filePath, content, 0o600); err != nil {
		t.Fatal(err)
	}
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{APIBaseURL: server.URL}})
	spec := snapshotArtifactUploadSpec{ArtifactID: 7, Path: "/upload", Token: "task-token", MaxBytes: 1024}

	t.Setenv("HFL_INSECURE_TLS", "0")
	if _, err := engine.uploadSnapshotArtifact(
		context.Background(), spec, filePath, "report.txt", "application/octet-stream",
	); err == nil {
		t.Fatal("expected strict TLS verification to reject the self-signed server")
	}

	t.Setenv("HFL_INSECURE_TLS", "1")
	if _, err := engine.uploadSnapshotArtifact(
		context.Background(), spec, filePath, "report.txt", "application/octet-stream",
	); err != nil {
		t.Fatalf("expected configured insecure TLS upload to succeed: %v", err)
	}
}

func TestZipDirectoryContentsToFileEnforcesArtifactLimit(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "large.bin"), bytes.Repeat([]byte("x"), 4096), 0o600); err != nil {
		t.Fatal(err)
	}
	destination := filepath.Join(t.TempDir(), "download.zip")
	if err := zipDirectoryContentsToFile(root, destination, 16); err == nil {
		t.Fatal("expected configured artifact limit to reject ZIP output")
	}
	if _, err := os.Stat(destination); !os.IsNotExist(err) {
		t.Fatalf("partial ZIP was not removed: %v", err)
	}
}

func TestZipDirectoryContentsToFileDoesNotFollowSymlinks(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink creation requires additional privileges on Windows")
	}
	root := t.TempDir()
	outside := filepath.Join(t.TempDir(), "secret.txt")
	if err := os.WriteFile(outside, []byte("must not be archived"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(root, "linked-secret")); err != nil {
		t.Fatal(err)
	}
	destination := filepath.Join(t.TempDir(), "download.zip")
	if err := zipDirectoryContentsToFile(root, destination, 1024); err != nil {
		t.Fatal(err)
	}
	archive, err := zip.OpenReader(destination)
	if err != nil {
		t.Fatal(err)
	}
	defer archive.Close()
	if len(archive.File) != 0 {
		t.Fatalf("symlink target escaped into artifact: %#v", archive.File)
	}
}

func TestParseKopiaPackedBytesJSON(t *testing.T) {
	got := parseKopiaPackedBytes(`{"totalPackedSize": 2048}`)
	if got != 2048 {
		t.Fatalf("parseKopiaPackedBytes() = %d, want 2048", got)
	}
}

func TestParseKopiaPackedBytesText(t *testing.T) {
	got := parseKopiaPackedBytes("Total Packed: 2 MB (compression 80%)")
	if got != 2*1024*1024 {
		t.Fatalf("parseKopiaPackedBytes() = %d, want %d", got, 2*1024*1024)
	}
}
