package engine

import (
	"archive/zip"
	"bytes"
	"context"
	"fmt"
	"io/fs"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"
	"time"

	"hyperfilelens/agent/internal/model"
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
	return vfs.RepositoryMountPoint(vfs.UnixDataDir(), repositoryID, 0)
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

func TestNASRepositoryWriteDeniedIsLimitedToSMBPermissionErrors(t *testing.T) {
	smb := mustNASSpec(t, map[string]any{
		"protocol": "smb", "server": "10.0.0.15", "share": "backup",
		"username": "backup", "password": "secret", "mount_point": testRepositoryMountPoint(t, 42),
	})
	if !isNASRepositoryWriteDenied(*smb, fs.ErrPermission) {
		t.Fatal("expected SMB permission error to be classified")
	}
	nfs := *smb
	nfs.Protocol = "nfs"
	if isNASRepositoryWriteDenied(nfs, fs.ErrPermission) {
		t.Fatal("did not expect NFS permission error to be classified")
	}
	if isNASRepositoryWriteDenied(*smb, fs.ErrNotExist) {
		t.Fatal("did not expect non-permission error to be classified")
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
	if got := repositoryCommandFailureMessage(process.Result{Stderr: "access denied"}, fmt.Errorf("exit 1")); got != "access denied" {
		t.Fatalf("expected stderr reason, got %q", got)
	}
	if got := repositoryCommandFailureMessage(process.Result{Stdout: "repository unavailable"}, fmt.Errorf("exit 1")); got != "repository unavailable" {
		t.Fatalf("expected stdout reason, got %q", got)
	}
	if got := repositoryCommandFailureMessage(process.Result{}, fmt.Errorf("exit 1")); got != "exit 1" {
		t.Fatalf("expected fallback error, got %q", got)
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
	args := managedBackupSnapshotArgs("/tmp/repo.config", "/tmp/source")

	if slices.Contains(args, "--progress-interval=3s") {
		t.Fatalf("snapshot args must not include unsupported --progress-interval flag: %#v", args)
	}
	if !slices.Contains(args, "--progress") {
		t.Fatalf("expected snapshot args to include --progress: %#v", args)
	}
	if !slices.Contains(args, "--json") {
		t.Fatalf("expected snapshot args to include --json: %#v", args)
	}
	if got := args[len(args)-2]; got != "/tmp/source" {
		t.Fatalf("expected source path before --json, got %q in %#v", got, args)
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
	if selection.modifiedAt != "2026-08-12 11:15:59 CST" {
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
