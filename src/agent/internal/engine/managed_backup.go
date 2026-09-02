package engine

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log/slog"
	"math"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	agentdisk "hyperfilelens/agent/internal/platform/disk"
	"hyperfilelens/agent/internal/platform/kopia"
	"hyperfilelens/agent/internal/platform/process"
	"hyperfilelens/agent/internal/platform/tlsclient"
	"hyperfilelens/agent/internal/platform/vfs"
	nassvc "hyperfilelens/agent/internal/service/nas"
)

const (
	managedRepositoryFSOperationTimeout  = 30 * time.Second
	managedRepositoryKopiaCommandTimeout = 2 * time.Minute
	kopiaEstimatedUsageFactor            = 1.05
	backupOperationTagKey                = "hfl-operation"
	backupOperationIDMaxLength           = 128
	repositoryAlreadyExistsCode          = "STORAGE.REPOSITORY_ALREADY_EXISTS"
	repositoryAlreadyExistsMessage       = "A Kopia repository already exists at the selected location. Import is not supported in this version. Choose a different storage location."
	nasRepositoryWriteDeniedCode         = "NAS_REPOSITORY_WRITE_DENIED"
	nasRepositoryWriteDeniedMessage      = "The NAS share was mounted, but the Agent does not have permission to write repository data."
	snapshotFailureSampleLimit           = 20
	snapshotFailurePathLimit             = 1024
	snapshotFailureErrorLimit            = 2048
)

type repositoryPrepareMode uint8

const (
	repositoryPrepareConnect repositoryPrepareMode = iota
	repositoryPrepareInitialize
)

type repositorySpec struct {
	ID              int64
	Type            string
	Path            string
	BasePath        string
	Layout          string
	Bucket          string
	Region          string
	Endpoint        string
	Prefix          string
	AccessKeyID     string
	SecretAccessKey string
	S3URLStyle      string
	S3URLStyleFlag  bool
	S3Platform      string
	KopiaPassword   string
	UseTLS          bool
	ConfigFile      string
	Subdir          string
	TargetNAS       *nassvc.Spec
	ServerURL       string
	ServerUsername  string
	ServerPassword  string
	ServerCert      string
	SessionID       string
	Ownership       *repositoryOwnership
}

type repositoryOwnership struct {
	DeploymentUUID string `json:"deployment_uuid"`
	RepositoryUUID string `json:"repository_uuid"`
	LocationDigest string `json:"location_digest"`
	FormatVersion  int    `json:"format_version"`
	Signature      string `json:"signature"`
	MarkerPath     string `json:"marker_path"`
}

var (
	kopiaS3URLStyleCapabilities sync.Map
	backupOperationIDPattern    = regexp.MustCompile(`^[A-Za-z0-9._-]+$`)
	snapshotFatalCountPattern   = regexp.MustCompile(`(?i)found\s+(\d+)\s+fatal\s+error`)
)

func parseRepositorySpec(raw any) (repositorySpec, bool, error) {
	data, ok := raw.(map[string]any)
	if !ok || len(data) == 0 {
		return repositorySpec{}, false, nil
	}
	spec := repositorySpec{
		Type:            strings.ToLower(strings.TrimSpace(stringValue(data["type"]))),
		Path:            strings.TrimSpace(stringValue(data["path"])),
		BasePath:        strings.TrimSpace(stringValue(data["base_path"])),
		Layout:          strings.ToLower(strings.TrimSpace(stringValue(data["layout"]))),
		Bucket:          strings.TrimSpace(stringValue(data["bucket"])),
		Region:          strings.TrimSpace(stringValue(data["region"])),
		Endpoint:        strings.TrimSpace(stringValue(data["endpoint"])),
		Prefix:          strings.TrimSpace(stringValue(data["prefix"])),
		AccessKeyID:     strings.TrimSpace(stringValue(data["access_key_id"])),
		SecretAccessKey: strings.TrimSpace(stringValue(data["secret_access_key"])),
		S3URLStyle:      strings.ToLower(strings.TrimSpace(stringValue(data["s3_url_style"]))),
		S3Platform:      strings.ToLower(strings.TrimSpace(stringValue(data["platform"]))),
		KopiaPassword:   strings.TrimSpace(stringValue(data["kopia_password"])),
		ConfigFile:      strings.TrimSpace(stringValue(data["config_file"])),
		Subdir:          strings.TrimSpace(stringValue(data["subdir"])),
		UseTLS:          boolValue(data["use_tls"], true),
		ServerURL:       strings.TrimSpace(stringValue(data["url"])),
		ServerUsername:  strings.TrimSpace(stringValue(data["username"])),
		ServerPassword:  strings.TrimSpace(stringValue(data["password"])),
		ServerCert:      strings.TrimSpace(stringValue(data["server_cert_fingerprint"])),
		SessionID:       strings.TrimSpace(stringValue(data["session_id"])),
	}
	if spec.Type == "nas" {
		nasRaw, ok := data["nas"].(map[string]any)
		if !ok || len(nasRaw) == 0 {
			return repositorySpec{}, false, fmt.Errorf("repository.nas is required for nas repositories")
		}
		targetNAS, err := nassvc.ParseSpec(nasRaw)
		if err != nil {
			return repositorySpec{}, false, err
		}
		spec.TargetNAS = &targetNAS
	}
	if id, ok := int64Value(data["id"]); ok {
		spec.ID = id
	}
	if ownershipRaw, ok := data["ownership"].(map[string]any); ok && len(ownershipRaw) > 0 {
		spec.Ownership = &repositoryOwnership{
			DeploymentUUID: strings.TrimSpace(stringValue(ownershipRaw["deployment_uuid"])),
			RepositoryUUID: strings.TrimSpace(stringValue(ownershipRaw["repository_uuid"])),
			LocationDigest: strings.TrimSpace(stringValue(ownershipRaw["location_digest"])),
			FormatVersion:  intValue(ownershipRaw["format_version"]),
			Signature:      strings.TrimSpace(stringValue(ownershipRaw["signature"])),
			MarkerPath:     strings.TrimSpace(stringValue(ownershipRaw["marker_path"])),
		}
		if err := validateRepositoryOwnership(*spec.Ownership); err != nil {
			return repositorySpec{}, false, err
		}
	}
	if spec.Type == "" {
		return repositorySpec{}, false, fmt.Errorf("repository.type is required")
	}
	switch spec.Type {
	case "s3":
		if spec.Bucket == "" {
			return repositorySpec{}, false, fmt.Errorf("repository.bucket is required for s3 repositories")
		}
		if spec.S3URLStyle == "" {
			spec.S3URLStyle = "auto"
		}
		switch spec.S3URLStyle {
		case "auto", "virtual_hosted", "path":
		default:
			return repositorySpec{}, false, fmt.Errorf("unsupported repository.s3_url_style %q", spec.S3URLStyle)
		}
	case "proxy_fs":
		if spec.Path == "" {
			return repositorySpec{}, false, fmt.Errorf("repository.path is required for proxy_fs repositories")
		}
		if spec.Layout != "" {
			if _, _, err := validateManagedProxyFSRepositoryPath(spec); err != nil {
				return repositorySpec{}, false, err
			}
		}
	case "nas":
		if spec.Subdir == "" {
			return repositorySpec{}, false, fmt.Errorf("repository.subdir is required for nas repositories")
		}
	case "kopia_server":
		if spec.ServerURL == "" {
			return repositorySpec{}, false, fmt.Errorf("repository.url is required for kopia_server repositories")
		}
	default:
		return repositorySpec{}, false, fmt.Errorf("unsupported repository type %q", spec.Type)
	}
	return spec, true, nil
}

func resolveKopiaS3URLStyleCapability(ctx context.Context, bin string, spec *repositorySpec) error {
	if spec == nil || spec.Type != "s3" || spec.S3URLStyle == "auto" {
		return nil
	}
	cacheKey := kopiaCapabilityCacheKey(bin)
	if cached, ok := kopiaS3URLStyleCapabilities.Load(cacheKey); ok {
		if cached.(bool) {
			spec.S3URLStyleFlag = true
			return nil
		}
		if spec.S3URLStyle == "virtual_hosted" {
			return fmt.Errorf(
				"repository requires virtual-hosted S3 URLs, but Kopia does not support --url-style; use the HyperFileLens-built Kopia artifact",
			)
		}
		return nil
	}
	res, err := runProcessWithTimeout(
		ctx,
		10*time.Second,
		bin,
		[]string{"repository", "create", "s3", "--help"},
		nil,
		"",
	)
	supports := strings.Contains(res.Stdout+"\n"+res.Stderr, "--url-style")
	if err == nil {
		kopiaS3URLStyleCapabilities.Store(cacheKey, supports)
		if supports {
			spec.S3URLStyleFlag = true
			return nil
		}
	}
	if spec.S3URLStyle == "virtual_hosted" {
		return fmt.Errorf(
			"repository requires virtual-hosted S3 URLs, but Kopia does not support --url-style; use the HyperFileLens-built Kopia artifact",
		)
	}
	return nil
}

func kopiaCapabilityCacheKey(bin string) string {
	info, err := os.Stat(bin)
	if err != nil {
		return filepath.Clean(bin)
	}
	return fmt.Sprintf("%s\x00%d\x00%d", filepath.Clean(bin), info.Size(), info.ModTime().UnixNano())
}

func s3ConnectionFingerprint(spec repositorySpec) (string, error) {
	payload := struct {
		Bucket          string `json:"bucket"`
		Region          string `json:"region"`
		Endpoint        string `json:"endpoint"`
		Prefix          string `json:"prefix"`
		AccessKeyID     string `json:"access_key_id"`
		SecretAccessKey string `json:"secret_access_key"`
		KopiaPassword   string `json:"kopia_password"`
		UseTLS          bool   `json:"use_tls"`
		S3URLStyle      string `json:"s3_url_style"`
	}{
		Bucket:          spec.Bucket,
		Region:          spec.Region,
		Endpoint:        spec.Endpoint,
		Prefix:          spec.Prefix,
		AccessKeyID:     spec.AccessKeyID,
		SecretAccessKey: spec.SecretAccessKey,
		KopiaPassword:   spec.KopiaPassword,
		UseTLS:          spec.UseTLS,
		S3URLStyle:      spec.S3URLStyle,
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:]), nil
}

func s3ConnectionFingerprintPath(configFile string) string {
	return configFile + ".connection.sha256"
}

func invalidateChangedS3Connection(configFile string, cacheDir string, spec repositorySpec) error {
	if _, err := os.Stat(configFile); errors.Is(err, os.ErrNotExist) {
		return nil
	} else if err != nil {
		return err
	}
	want, err := s3ConnectionFingerprint(spec)
	if err != nil {
		return err
	}
	current, _ := os.ReadFile(s3ConnectionFingerprintPath(configFile))
	if strings.TrimSpace(string(current)) == want {
		return nil
	}
	if err := os.Remove(configFile); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if strings.TrimSpace(cacheDir) != "" {
		if err := os.RemoveAll(cacheDir); err != nil {
			return err
		}
	}
	return nil
}

func writeS3ConnectionFingerprint(configFile string, spec repositorySpec) error {
	fingerprint, err := s3ConnectionFingerprint(spec)
	if err != nil {
		return err
	}
	path := s3ConnectionFingerprintPath(configFile)
	temporary := path + ".part"
	if err := os.WriteFile(temporary, []byte(fingerprint+"\n"), 0o600); err != nil {
		return err
	}
	return os.Rename(temporary, path)
}

func repositoryNASPath(spec repositorySpec) (string, error) {
	if spec.TargetNAS == nil {
		return "", fmt.Errorf("repository.nas is required for nas repositories")
	}
	subdir := filepath.Clean(strings.TrimSpace(spec.Subdir))
	if subdir == "." || subdir == "" || filepath.IsAbs(subdir) || strings.HasPrefix(subdir, "..") {
		return "", fmt.Errorf("invalid nas repository subdir %q", spec.Subdir)
	}
	root := filepath.Clean(nassvc.ResolvedMountPoint(spec.TargetNAS.MountPoint))
	full := filepath.Clean(filepath.Join(root, subdir))
	rel, err := filepath.Rel(root, full)
	if err != nil || rel == "." || strings.HasPrefix(rel, "..") || filepath.IsAbs(rel) {
		return "", fmt.Errorf("nas repository path escapes mount point")
	}
	return full, nil
}

func runWithTimeout(ctx context.Context, timeout time.Duration, fn func() error) error {
	if fn == nil {
		return nil
	}
	if timeout <= 0 {
		return fn()
	}
	done := make(chan error, 1)
	go func() {
		done <- fn()
	}()
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case err := <-done:
		return err
	case <-timer.C:
		return fmt.Errorf("operation timed out after %s", timeout)
	}
}

func runProcessWithTimeout(
	ctx context.Context,
	timeout time.Duration,
	bin string,
	args []string,
	env map[string]string,
	workDir string,
) (process.Result, error) {
	if timeout <= 0 {
		return process.Run(ctx, bin, args, env, workDir)
	}
	runCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	res, err := process.Run(runCtx, bin, args, env, workDir)
	if runCtx.Err() == context.DeadlineExceeded {
		return res, fmt.Errorf("process timed out after %s", timeout)
	}
	return res, err
}

func boolValue(raw any, defaultValue bool) bool {
	switch value := raw.(type) {
	case bool:
		return value
	case string:
		v := strings.ToLower(strings.TrimSpace(value))
		if v == "" {
			return defaultValue
		}
		return v == "1" || v == "true" || v == "yes" || v == "on"
	default:
		return defaultValue
	}
}

func int64Value(raw any) (int64, bool) {
	switch value := raw.(type) {
	case int:
		return int64(value), true
	case int32:
		return int64(value), true
	case int64:
		return value, true
	case float64:
		return int64(value), true
	case string:
		parsed, err := strconv.ParseInt(strings.TrimSpace(value), 10, 64)
		if err == nil {
			return parsed, true
		}
	}
	return 0, false
}

func exactInt64Value(raw any) (int64, bool) {
	switch value := raw.(type) {
	case float64:
		if math.IsNaN(value) || math.IsInf(value, 0) || math.Trunc(value) != value {
			return 0, false
		}
		if value < math.MinInt64 || value >= math.MaxInt64 {
			return 0, false
		}
		return int64(value), true
	default:
		return int64Value(raw)
	}
}

func stringValue(raw any) string {
	if raw == nil {
		return ""
	}
	return fmt.Sprint(raw)
}

func (e *Engine) repositoryConfigPath(spec repositorySpec) string {
	if spec.ConfigFile != "" {
		return spec.ConfigFile
	}
	base := strings.TrimSpace(e.current().DataDir)
	if base == "" {
		base = os.TempDir()
	}
	filename := "repository.config"
	if spec.Type == "kopia_server" {
		token := repositoryServerConfigToken(spec)
		if spec.ID > 0 {
			filename = fmt.Sprintf("repo-%d-server-%s.config", spec.ID, token)
		} else {
			filename = fmt.Sprintf("server-%s.config", token)
		}
	} else if spec.Type == "nas" && spec.ID > 0 && strings.TrimSpace(spec.Subdir) != "" {
		namespace := strings.TrimSpace(spec.Subdir)
		if spec.TargetNAS != nil {
			namespace += "|" + strings.TrimSpace(spec.TargetNAS.MountPoint)
		}
		filename = fmt.Sprintf("repo-%d-nas-%s.config", spec.ID, repositoryNamespaceToken(namespace))
	} else if spec.ID > 0 {
		filename = fmt.Sprintf("repo-%d.config", spec.ID)
	}
	return filepath.Join(vfs.AgentCacheDir(base), "repositories", filename)
}

func repositoryNamespaceToken(namespace string) string {
	sum := sha256.Sum256([]byte(strings.TrimSpace(namespace)))
	return hex.EncodeToString(sum[:])[:16]
}

func repositoryServerConfigToken(spec repositorySpec) string {
	source := strings.TrimSpace(spec.SessionID)
	if source == "" {
		source = strings.TrimSpace(spec.ServerURL)
	}
	if source == "" {
		source = "server"
	}
	sum := sha256.Sum256([]byte(source))
	token := sanitizeSessionToken(source)
	if len(token) > 48 {
		token = token[:48]
	}
	return fmt.Sprintf("%s-%s", token, hex.EncodeToString(sum[:])[:12])
}

func (e *Engine) prepareManagedRepository(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
	mode repositoryPrepareMode,
) (string, map[string]string, map[string]any, repositorySpec, string) {
	spec, ok, err := parseRepositorySpec(p.Extra["repository"])
	if err != nil {
		return "", nil, nil, repositorySpec{}, err.Error()
	}
	if !ok {
		return "", nil, nil, repositorySpec{}, "repository payload is required"
	}

	bin, err := e.kopiaBin(ctx)
	if err != nil {
		return "", nil, nil, repositorySpec{}, err.Error()
	}
	if err := resolveKopiaS3URLStyleCapability(ctx, bin, &spec); err != nil {
		return "", nil, nil, repositorySpec{}, err.Error()
	}

	configFile := e.repositoryConfigPath(spec)
	if mkErr := os.MkdirAll(filepath.Dir(configFile), 0o700); mkErr != nil {
		return "", nil, nil, repositorySpec{}, mkErr.Error()
	}
	lockStarted := time.Now()
	return withRepositoryPrepareLock(ctx, configFile, func() (string, map[string]string, map[string]any, repositorySpec, string) {
		slog.Info("managed_repository", "event", "prepare_lock_acquired", "task_id", taskID, "wait_ms", time.Since(lockStarted).Milliseconds())
		return e.prepareManagedRepositoryLocked(ctx, rep, taskID, p, spec, bin, configFile, mode)
	})
}

func (e *Engine) prepareManagedRepositoryLocked(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
	spec repositorySpec,
	bin string,
	configFile string,
	mode repositoryPrepareMode,
) (string, map[string]string, map[string]any, repositorySpec, string) {
	allowOwnershipAdoption := false
	skipOwnershipCheck := false
	repairMount := false
	if value, present := p.Extra["allow_ownership_adoption"]; present {
		if parsed, valid := payloadBoolValue(value); valid {
			allowOwnershipAdoption = parsed
		}
	}
	if value, present := p.Extra["skip_ownership_check"]; present {
		if parsed, valid := payloadBoolValue(value); valid {
			// This relaxation is reserved for the restore target's temporary
			// read-only probe.  Initialization and ordinary health/cleanup
			// operations must retain the ownership boundary.
			probe := stringValue(p.Extra["probe"])
			skipOwnershipCheck = parsed && mode == repositoryPrepareConnect &&
				(probe == "restore_target_validation" || probe == "restore_execution")
		}
	}
	if value, present := p.Extra["repair_mount"]; present {
		if parsed, valid := payloadBoolValue(value); valid {
			repairMount = parsed
		}
	}
	env := map[string]string{
		"KOPIA_CHECK_FOR_UPDATES": "false",
	}
	if password := repositoryPasswordEnvValue(spec); password != "" {
		env["KOPIA_PASSWORD"] = password
	}
	for key, value := range p.Env {
		env[key] = value
	}
	// A shared Kopia cache can retain an encrypted format blob from another
	// repository. Creating a repository with a different password then writes
	// the new format but fails to reopen it with "invalid repository password".
	// Managed repositories must therefore own a cache scoped to their config.
	env["KOPIA_CACHE_DIRECTORY"] = managedRepositoryCacheDir(e.current(), configFile)
	var envErr error
	env, envErr = ensureKopiaProcessEnv(e.current(), env)
	if envErr != nil {
		return "", nil, nil, repositorySpec{}, envErr.Error()
	}
	cachePolicy, cachePolicyErr := kopiaCachePolicyFromPayload(p)
	if cachePolicyErr != nil {
		return "", nil, nil, spec, cachePolicyErr.Error()
	}
	cacheArgs := cachePolicy.flags()
	if spec.Type == "s3" {
		env["AWS_ACCESS_KEY_ID"] = spec.AccessKeyID
		env["AWS_SECRET_ACCESS_KEY"] = spec.SecretAccessKey
		if err := invalidateChangedS3Connection(configFile, env["KOPIA_CACHE_DIRECTORY"], spec); err != nil {
			return "", nil, nil, repositorySpec{}, err.Error()
		}
	}
	if spec.Type == "kopia_server" {
		if spec.ServerUsername != "" {
			env["KOPIA_SERVER_USERNAME"] = spec.ServerUsername
		}
		if spec.ServerPassword != "" {
			env["KOPIA_SERVER_PASSWORD"] = spec.ServerPassword
		}
	}
	if spec.Type == "nas" {
		nassvc.LogSpec("repository_mount_ensure_begin", *spec.TargetNAS, "task_id", taskID)
		mountService := nassvc.NewService()
		var mountErr error
		if mode == repositoryPrepareInitialize || repairMount {
			mountErr = mountService.EnsureMountedForWrite(ctx, *spec.TargetNAS)
		} else {
			mountErr = mountService.EnsureMounted(ctx, *spec.TargetNAS)
		}
		if mountErr != nil {
			nassvc.LogSpec("repository_mount_ensure_failed", *spec.TargetNAS, "task_id", taskID, "err", mountErr.Error())
			return "", nil, nasRepositoryMountErrorResult(mountErr), repositorySpec{}, mountErr.Error()
		}
		nassvc.LogSpec("repository_mount_ensure_ok", *spec.TargetNAS, "task_id", taskID)
		repoPath, pathErr := repositoryNASPath(spec)
		if pathErr != nil {
			return "", nil, nil, repositorySpec{}, pathErr.Error()
		}
		// An existing, ownership-bound repository must be verified before any
		// write-intent probe.  A database claim is not by itself permission to
		// modify whatever currently appears at the NAS path; the durable marker
		// is the physical boundary.  Initialization is different: it owns the
		// empty path only after the write precheck below succeeds.
		if mode == repositoryPrepareConnect && spec.Ownership != nil && !allowOwnershipAdoption && !skipOwnershipCheck {
			if ownershipErr := verifyFilesystemRepositoryOwnership(spec, false); ownershipErr != nil {
				return "", nil, map[string]any{
					"error_code": "REPOSITORY_OWNERSHIP_INVALID",
				}, spec, ownershipErr.Error()
			}
		}
		if mode == repositoryPrepareInitialize || repairMount {
			nassvc.LogSpec("repository_mkdir_begin", *spec.TargetNAS, "task_id", taskID, "repo_path", repoPath)
			if mkErr := runWithTimeout(ctx, managedRepositoryFSOperationTimeout, func() error {
				return os.MkdirAll(repoPath, 0o755)
			}); mkErr != nil {
				nassvc.LogSpec("repository_mkdir_failed", *spec.TargetNAS, "task_id", taskID, "repo_path", repoPath, "err", mkErr.Error())
				if result, message, classified := classifyNASRepositoryWriteError(*spec.TargetNAS, mkErr, "repository_directory_create"); classified {
					return "", nil, result, repositorySpec{}, message
				}
				return "", nil, nil, repositorySpec{}, mkErr.Error()
			}
			nassvc.LogSpec("repository_mkdir_ok", *spec.TargetNAS, "task_id", taskID, "repo_path", repoPath)
			if probeErr := probeNASRepositoryWritable(repoPath); probeErr != nil {
				nassvc.LogSpec("repository_write_probe_failed", *spec.TargetNAS, "task_id", taskID, "repo_path", repoPath, "err", probeErr.Error())
				if result, message, classified := classifyNASRepositoryWriteError(*spec.TargetNAS, probeErr, "repository_write_probe"); classified {
					return "", nil, result, repositorySpec{}, message
				}
				return "", nil, nil, repositorySpec{}, probeErr.Error()
			}
		}
		spec.Path = repoPath
	}
	ownershipExistedBeforeInitialize := false
	if mode == repositoryPrepareInitialize && spec.Type != "s3" && spec.Ownership != nil {
		var ownershipCheckErr error
		ownershipExistedBeforeInitialize, ownershipCheckErr = filesystemRepositoryOwnershipMatches(spec)
		if ownershipCheckErr != nil {
			return "", nil, map[string]any{
				"error_code": "REPOSITORY_OWNERSHIP_INVALID",
			}, spec, ownershipCheckErr.Error()
		}
	}
	if mode == repositoryPrepareInitialize && spec.Type == "proxy_fs" && spec.Layout == "managed_subdir_v1" {
		exists, pathErr := managedProxyFSCreatePathExists(spec)
		if pathErr != nil {
			return "", nil, nil, repositorySpec{}, pathErr.Error()
		}
		if exists && !ownershipExistedBeforeInitialize {
			return "", nil, map[string]any{
				"error_code": repositoryAlreadyExistsCode,
			}, spec, repositoryAlreadyExistsMessage
		}
	}
	if mode == repositoryPrepareInitialize && spec.Type != "s3" && spec.Ownership != nil {
		if ownershipErr := claimFilesystemRepositoryOwnership(spec); ownershipErr != nil {
			if errors.Is(ownershipErr, errRepositoryDirectoryContainsData) {
				return "", nil, map[string]any{
					"error_code": repositoryAlreadyExistsCode,
				}, spec, repositoryAlreadyExistsMessage
			}
			return "", nil, map[string]any{
				"error_code": "REPOSITORY_OWNERSHIP_INVALID",
			}, spec, ownershipErr.Error()
		}
	}

	_ = sendProgress(ctx, rep, taskID, orchestrationProgressPayload(
		"repository_prepare",
		"Connecting backup repository",
		map[string]any{"repo_type": spec.Type},
	))

	result := map[string]any{
		"config_file":        configFile,
		"repository_create":  nil,
		"repository_connect": nil,
	}
	if cacheErr := applyManagedKopiaCachePolicy(ctx, bin, configFile, env, cachePolicy); cacheErr != nil {
		// A running repository operation must not be made unavailable solely
		// because its cache policy could not be refreshed. Keep the last valid
		// config and report the deferred application in the task result.
		result["kopia_cache_policy_error"] = cacheErr.Error()
		slog.Warn("managed_repository", "event", "cache_policy_apply_deferred", "task_id", taskID, "err", cacheErr.Error())
	}
	if spec.Type == "s3" && mode != repositoryPrepareInitialize && !skipOwnershipCheck {
		client, ownershipErr := newS3CleanupClient(spec)
		if ownershipErr == nil {
			ownershipErr = verifyS3RepositoryOwnership(ctx, client, spec)
		}
		if ownershipErr != nil {
			result["error_code"] = "REPOSITORY_OWNERSHIP_INVALID"
			return "", nil, result, spec, ownershipErr.Error()
		}
		result["ownership_verified"] = true
	}
	if spec.Type != "s3" && spec.Ownership != nil && !skipOwnershipCheck {
		var ownershipErr error
		if mode == repositoryPrepareConnect && !allowOwnershipAdoption {
			ownershipErr = verifyFilesystemRepositoryOwnership(spec, false)
		} else {
			ownershipErr = checkFilesystemRepositoryOwnershipIfPresent(spec)
		}
		if ownershipErr != nil {
			result["error_code"] = "REPOSITORY_OWNERSHIP_INVALID"
			return "", nil, result, spec, ownershipErr.Error()
		}
	}
	if spec.Type == "kopia_server" && mode == repositoryPrepareInitialize {
		return "", nil, result, spec, "kopia_server repositories cannot be initialized"
	}
	statusArgs := []string{"--config-file=" + configFile, "repository", "status"}
	runStatus := func(event string) (process.Result, error) {
		started := time.Now()
		slog.Info("managed_repository", "event", event+"_begin", "task_id", taskID, "repo_type", spec.Type)
		statusRes, statusErr := runProcessWithTimeout(
			ctx, managedRepositoryKopiaCommandTimeout, bin, statusArgs, env, "",
		)
		result["repository_status"] = redactedRepositoryCommandResult(statusRes, spec, p)
		slog.Info("managed_repository", "event", event+"_finished", "task_id", taskID, "repo_type", spec.Type, "duration_ms", time.Since(started).Milliseconds(), "ok", statusErr == nil)
		return statusRes, statusErr
	}
	runConnect := func(event string) (process.Result, error) {
		started := time.Now()
		connectArgs := append(repositoryArgs(configFile, spec, false), cacheArgs...)
		slog.Info("managed_repository", "event", event+"_begin", "task_id", taskID, "repo_type", spec.Type)
		connectRes, connectErr := runProcessWithTimeout(
			ctx, managedRepositoryKopiaCommandTimeout, bin, connectArgs, env, "",
		)
		result["repository_connect"] = redactedRepositoryCommandResult(connectRes, spec, p)
		slog.Info("managed_repository", "event", event+"_finished", "task_id", taskID, "repo_type", spec.Type, "duration_ms", time.Since(started).Milliseconds(), "ok", connectErr == nil)
		return connectRes, connectErr
	}

	statusVerified := false
	if mode == repositoryPrepareInitialize {
		createArgs := append(repositoryArgs(configFile, spec, true), cacheArgs...)
		started := time.Now()
		slog.Info("managed_repository", "event", "create_begin", "task_id", taskID, "repo_type", spec.Type)
		createRes, createErr := runProcessWithTimeout(ctx, managedRepositoryKopiaCommandTimeout, bin, createArgs, env, "")
		result["repository_create"] = redactedRepositoryCommandResult(createRes, spec, p)
		if createErr != nil {
			if repositoryCreateAlreadyExists(createRes) && ownershipExistedBeforeInitialize {
				slog.Info("managed_repository", "event", "create_retry_owned_repository", "task_id", taskID, "repo_type", spec.Type)
				if connectRes, connectErr := runConnect("connect_owned_retry"); connectErr != nil {
					return "", nil, result, spec, repositoryCommandFailureMessage(connectRes, connectErr, spec, p)
				}
				statusRes, statusErr := runStatus("status_owned_retry")
				if statusErr != nil {
					return "", nil, result, spec, repositoryCommandFailureMessage(statusRes, statusErr, spec, p)
				}
				statusVerified = true
			} else if repositoryCreateAlreadyExists(createRes) {
				result["error_code"] = repositoryAlreadyExistsCode
				slog.Info("managed_repository", "event", "create_rejected_existing", "task_id", taskID, "repo_type", spec.Type)
				return "", nil, result, spec, repositoryAlreadyExistsMessage
			} else {
				slog.Warn("managed_repository", "event", "create_failed", "task_id", taskID, "repo_type", spec.Type, "err", createErr.Error())
				return "", nil, result, spec, repositoryCommandFailureMessage(createRes, createErr, spec, p)
			}
		}
		slog.Info("managed_repository", "event", "create_ok", "task_id", taskID, "repo_type", spec.Type, "duration_ms", time.Since(started).Milliseconds())
	} else {
		if _, statErr := os.Stat(configFile); statErr == nil {
			if _, statusErr := runStatus("status_first"); statusErr == nil {
				if spec.Type != "s3" && spec.Ownership != nil && !skipOwnershipCheck {
					if ownershipErr := verifyFilesystemRepositoryOwnership(spec, allowOwnershipAdoption); ownershipErr != nil {
						return "", nil, result, spec, ownershipErr.Error()
					}
					result["ownership_verified"] = true
				}
				slog.Info("managed_repository", "event", "status_first_ok", "task_id", taskID, "repo_type", spec.Type)
				_ = sendProgress(ctx, rep, taskID, orchestrationProgressPayload(
					"repository_ready",
					"Repository ready",
					map[string]any{"config_file": configFile, "reused": true},
				))
				return configFile, env, result, spec, ""
			}
		} else if !errors.Is(statErr, os.ErrNotExist) {
			return "", nil, result, spec, statErr.Error()
		}

		connectRes, connectErr := runConnect("connect")
		if connectErr != nil {
			if _, statusErr := runStatus("status_after_connect_error"); statusErr == nil {
				slog.Info("managed_repository", "event", "connect_failed_status_ok", "task_id", taskID, "repo_type", spec.Type, "err", connectErr.Error())
				statusVerified = true
			} else {
				slog.Warn("managed_repository", "event", "connect_failed", "task_id", taskID, "repo_type", spec.Type, "err", connectErr.Error())
				return "", nil, result, spec, repositoryCommandFailureMessage(connectRes, connectErr, spec, p)
			}
		}
	}
	statusRes := process.Result{}
	var statusErr error
	if !statusVerified {
		statusRes, statusErr = runStatus("status")
	}
	if statusErr != nil {
		if mode == repositoryPrepareInitialize {
			slog.Warn("managed_repository", "event", "status_failed_after_create", "task_id", taskID, "repo_type", spec.Type, "err", statusErr.Error())
			return "", nil, result, spec, repositoryCommandFailureMessage(statusRes, statusErr, spec, p)
		}
		slog.Info("managed_repository", "event", "status_failed_reconnect_begin", "task_id", taskID, "repo_type", spec.Type, "err", statusErr.Error())
		connectRes, connectErr := runConnect("reconnect")
		if connectErr != nil {
			slog.Warn("managed_repository", "event", "reconnect_failed", "task_id", taskID, "repo_type", spec.Type, "err", connectErr.Error())
			return "", nil, result, spec, repositoryCommandFailureMessage(connectRes, connectErr, spec, p)
		}
		statusRes, statusErr = runStatus("status_after_reconnect")
		if statusErr != nil {
			slog.Warn("managed_repository", "event", "status_failed", "task_id", taskID, "repo_type", spec.Type, "err", statusErr.Error())
			return "", nil, result, spec, repositoryCommandFailureMessage(statusRes, statusErr, spec, p)
		}
	}
	slog.Info("managed_repository", "event", "status_ok", "task_id", taskID, "repo_type", spec.Type)
	if spec.Type != "s3" && spec.Ownership != nil && !skipOwnershipCheck {
		if ownershipErr := verifyFilesystemRepositoryOwnership(spec, allowOwnershipAdoption); ownershipErr != nil {
			return "", nil, result, spec, ownershipErr.Error()
		}
		result["ownership_verified"] = true
	}
	if spec.Type == "s3" {
		if err := writeS3ConnectionFingerprint(configFile, spec); err != nil {
			return "", nil, result, spec, err.Error()
		}
	}
	_ = sendProgress(ctx, rep, taskID, orchestrationProgressPayload(
		"repository_ready",
		"Repository ready",
		map[string]any{"config_file": configFile},
	))
	return configFile, env, result, spec, ""
}

func nasRepositoryMountErrorResult(err error) map[string]any {
	var mismatch *nassvc.MountSourceMismatchError
	if errors.As(err, &mismatch) {
		return map[string]any{"error_code": "NAS_MOUNT_SOURCE_MISMATCH", "stage": "mount_validation"}
	}
	var readOnly *nassvc.MountReadOnlyError
	if errors.As(err, &readOnly) {
		return map[string]any{"error_code": "NAS_REPOSITORY_READ_ONLY", "stage": "mount_validation"}
	}
	return map[string]any{"error_code": "NAS_MOUNT_FAILED", "stage": "mount"}
}

func probeNASRepositoryWritable(repositoryPath string) error {
	file, err := os.CreateTemp(repositoryPath, ".hfl-write-probe-*")
	if err != nil {
		return err
	}
	name := file.Name()
	if _, err = file.Write([]byte("hfl")); err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	removeErr := os.Remove(name)
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return removeErr
}

func classifyNASRepositoryWriteError(spec nassvc.Spec, err error, stage string) (map[string]any, string, bool) {
	code := ""
	message := ""
	if strings.Contains(strings.ToLower(err.Error()), "read-only file system") {
		code = "NAS_REPOSITORY_READ_ONLY"
		message = "NAS repository is mounted read-only. Enable write access and retry storage validation."
	} else if errors.Is(err, fs.ErrPermission) {
		code = nasRepositoryWriteDeniedCode
		message = nasRepositoryWriteDeniedMessage
	}
	if code == "" {
		return nil, "", false
	}
	return map[string]any{
		"error_code":   code,
		"protocol":     spec.Protocol,
		"stage":        stage,
		"mount_status": "mounted",
	}, message, true
}

func managedProxyFSCreatePathExists(spec repositorySpec) (bool, error) {
	path, _, err := validateManagedProxyFSRepositoryPath(spec)
	if err != nil {
		return false, err
	}
	_, err = os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return true, nil
}

func (e *Engine) runManagedRepositoryStatus(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
) (string, map[string]any, string) {
	configFile, env, result, spec, errMsg := e.prepareManagedRepository(ctx, rep, taskID, p, repositoryPrepareConnect)
	if errMsg != "" {
		if result == nil {
			result = map[string]any{}
		}
		if spec.Type != "" {
			result["repository_type"] = spec.Type
		}
		setRepositoryOwnershipErrorCode(result, errMsg)
		return "failed", result, errMsg
	}
	if result == nil {
		result = map[string]any{}
	}
	result["repository_type"] = spec.Type
	if spec.Path != "" {
		result["repository_path"] = spec.Path
	}
	healthOnly, _ := payloadBoolValue(p.Extra["health_only"])
	if configFile != "" && !healthOnly {
		if bin, err := e.kopiaBin(ctx); err == nil {
			appendRepositoryUsageMetrics(ctx, bin, configFile, env, spec, result)
		}
	}
	return "success", result, ""
}

func (e *Engine) runManagedRepositoryInitialize(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
) (string, map[string]any, string) {
	configFile, env, result, spec, errMsg := e.prepareManagedRepository(ctx, rep, taskID, p, repositoryPrepareInitialize)
	if errMsg != "" {
		if result == nil {
			result = map[string]any{}
		}
		if spec.Type != "" {
			result["repository_type"] = spec.Type
		}
		setRepositoryOwnershipErrorCode(result, errMsg)
		return "failed", result, errMsg
	}
	if result == nil {
		result = map[string]any{}
	}
	result["repository_type"] = spec.Type
	if spec.Path != "" {
		result["repository_path"] = spec.Path
	}
	if configFile != "" {
		if bin, err := e.kopiaBin(ctx); err == nil {
			appendRepositoryUsageMetrics(ctx, bin, configFile, env, spec, result)
		}
	}
	return "success", result, ""
}

func setRepositoryOwnershipErrorCode(result map[string]any, message string) {
	normalized := strings.ToLower(strings.TrimSpace(message))
	if strings.Contains(normalized, "repository ownership") ||
		strings.Contains(normalized, "physical repository ownership") ||
		strings.Contains(normalized, "managed repository") {
		result["error_code"] = "REPOSITORY_OWNERSHIP_INVALID"
	}
}

func (e *Engine) runManagedRepositoryMaintenance(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
) (string, map[string]any, string) {
	operationType := strings.ToLower(strings.TrimSpace(payloadStringValue(p.Extra["operation_type"])))
	if operationType != "maintenance.quick" && operationType != "maintenance.full" {
		return "failed", nil, fmt.Sprintf("unsupported repository operation %q", operationType)
	}
	ownerIdentity := payloadStringValue(p.Extra["owner_identity"])
	ownerUser, ownerHost := kopiaServerIdentity(ownerIdentity)
	if ownerUser == "" || ownerHost == "" {
		return "failed", nil, "valid owner_identity is required"
	}
	configFile, env, result, spec, errMsg := e.prepareManagedRepository(ctx, rep, taskID, p, repositoryPrepareConnect)
	if errMsg != "" {
		return "failed", result, errMsg
	}
	bin, err := e.kopiaBin(ctx)
	if err != nil {
		return "failed", result, err.Error()
	}
	maintenanceConfigFile := strings.TrimSuffix(configFile, filepath.Ext(configFile)) + ".maintenance.config"
	cachePolicy, cacheErr := kopiaCachePolicyFromPayload(p)
	if cacheErr != nil {
		return "failed", result, cacheErr.Error()
	}
	cacheArgs := cachePolicy.flags()
	if cacheErr := applyManagedKopiaCachePolicy(ctx, bin, maintenanceConfigFile, env, cachePolicy); cacheErr != nil {
		result["maintenance_kopia_cache_policy_error"] = cacheErr.Error()
		slog.Warn(
			"managed_repository",
			"event", "maintenance_cache_policy_apply_deferred",
			"task_id", taskID,
			"err", cacheErr.Error(),
		)
	}
	connectArgs := append(repositoryArgs(maintenanceConfigFile, spec, false), cacheArgs...)
	connectResult, connectErr := process.Run(ctx, bin, connectArgs, env, "")
	if connectErr != nil {
		statusArgs := []string{"--config-file=" + maintenanceConfigFile, "repository", "status"}
		statusResult, statusErr := process.Run(ctx, bin, statusArgs, env, "")
		if statusErr != nil {
			if result == nil {
				result = map[string]any{}
			}
			result["maintenance_repository_connect"] = commandResult(connectResult)
			result["maintenance_repository_status"] = commandResult(statusResult)
			return "failed", result, connectErr.Error()
		}
	}
	clientArgs := []string{
		"--config-file=" + maintenanceConfigFile,
		"repository", "set-client",
		"--username=" + ownerUser,
		"--hostname=" + ownerHost,
	}
	clientResult, clientErr := process.Run(ctx, bin, clientArgs, env, "")
	if result == nil {
		result = map[string]any{}
	}
	result["maintenance_client_configuration"] = commandResult(clientResult)
	if clientErr != nil {
		return "failed", result, clientErr.Error()
	}
	args := []string{
		"--config-file=" + maintenanceConfigFile,
		"maintenance", "run",
	}
	configureArgs := []string{
		"--config-file=" + maintenanceConfigFile,
		"maintenance", "set",
		"--owner=" + ownerIdentity,
		"--enable-quick=false",
		"--enable-full=false",
	}
	configureResult, configureErr := process.Run(ctx, bin, configureArgs, env, "")
	result["maintenance_configuration"] = commandResult(configureResult)
	if configureErr != nil {
		return "failed", result, configureErr.Error()
	}
	if operationType == "maintenance.full" {
		args = append(args, "--full")
	}
	_ = sendProgress(ctx, rep, taskID, map[string]any{
		"phase":          "repository_maintenance",
		"operation_type": operationType,
	})
	maintenanceResult, maintenanceErr := process.Run(ctx, bin, args, env, "")
	result["operation_type"] = operationType
	result["repository_type"] = spec.Type
	result["maintenance"] = commandResult(maintenanceResult)
	if maintenanceErr != nil {
		if ctx.Err() != nil {
			return "failed", result, "canceled"
		}
		return "failed", result, maintenanceErr.Error()
	}
	appendRepositoryUsageMetrics(ctx, bin, configFile, env, spec, result)
	return "success", result, ""
}

func (e *Engine) runManagedRepositoryOperation(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
) (string, map[string]any, string) {
	operationType := strings.ToLower(strings.TrimSpace(payloadStringValue(p.Extra["operation_type"])))
	if operationType == "cleanup.target" || operationType == "cleanup.repository" {
		return e.runManagedRepositoryCleanup(ctx, rep, taskID, p)
	}
	return e.runManagedRepositoryMaintenance(ctx, rep, taskID, p)
}

func repositoryPasswordEnvValue(spec repositorySpec) string {
	if spec.Type == "kopia_server" && spec.ServerPassword != "" {
		return spec.ServerPassword
	}
	return spec.KopiaPassword
}

func appendRepositoryUsageMetrics(
	ctx context.Context,
	bin string,
	configFile string,
	env map[string]string,
	spec repositorySpec,
	result map[string]any,
) {
	statsArgs := []string{"--config-file=" + configFile, "content", "stats", "--json"}
	statsRes, statsErr := runProcessWithTimeout(ctx, managedRepositoryKopiaCommandTimeout, bin, statsArgs, env, "")
	if statsErr != nil && snapshotBrowseJsonUnsupported(statsRes) {
		statsArgs = []string{"--config-file=" + configFile, "content", "stats"}
		statsRes, statsErr = runProcessWithTimeout(ctx, managedRepositoryKopiaCommandTimeout, bin, statsArgs, env, "")
	}
	result["content_stats"] = commandResult(statsRes)
	if statsErr == nil {
		packed := parseKopiaPackedBytes(statsRes.Stdout)
		estimated := uint64(float64(packed) * kopiaEstimatedUsageFactor)
		result["content_stats_bytes"] = packed
		result["estimated_usage_bytes"] = estimated
		result["usage_probe"] = map[string]any{
			"status": "success", "estimated_usage_bytes": estimated,
		}
	} else {
		result["usage_probe"] = map[string]any{
			"status": "failed", "error": "Unable to read repository usage.",
		}
	}

	spacePath := strings.TrimSpace(spec.Path)
	if spacePath == "" || (spec.Type != "nas" && spec.Type != "proxy_fs") {
		return
	}
	storage, err := agentdisk.Inspect(spacePath)
	if err != nil {
		result["capacity_probe"] = map[string]any{
			"status": "failed", "error": "Unable to read filesystem capacity.",
		}
		return
	}
	result["capacity_probe"] = map[string]any{
		"status": "success", "total_bytes": storage.Total,
	}
	result["space_info"] = map[string]any{
		"path":        spacePath,
		"mount_point": storage.MountPoint,
		"pool_key":    storage.PoolKey,
		"total_bytes": storage.Total,
		"used_bytes":  storage.Used,
		"free_bytes":  storage.Free,
	}
}

func parseKopiaPackedBytes(stdout string) uint64 {
	text := strings.TrimSpace(stdout)
	if text == "" {
		return 0
	}
	var payload map[string]any
	if err := json.Unmarshal([]byte(text), &payload); err == nil {
		for _, key := range []string{
			"totalPackedSize",
			"total_packed_size",
			"packedSize",
			"packed_size",
			"totalCompressedSize",
			"total_compressed_size",
			"totalSize",
			"total_size",
		} {
			if value, ok := uint64Value(payload[key]); ok && value > 0 {
				return value
			}
		}
		if stats, ok := payload["stats"].(map[string]any); ok {
			if nested := parseKopiaPackedBytes(mustJSONString(stats)); nested > 0 {
				return nested
			}
		}
	}
	return parseKopiaPackedBytesText(text)
}

func mustJSONString(value any) string {
	raw, err := json.Marshal(value)
	if err != nil {
		return ""
	}
	return string(raw)
}

func parseKopiaPackedBytesText(text string) uint64 {
	lower := strings.ToLower(text)
	re := regexp.MustCompile(`(?i)(?:total\s*)?packed\s*[:=]\s*([\d.,]+)\s*([kmgt]?ib|[kmgt]?b)?`)
	if match := re.FindStringSubmatch(lower); len(match) >= 2 {
		if parsed, ok := parseHumanSize(strings.TrimSpace(match[1] + " " + strings.TrimSpace(match[2]))); ok {
			return parsed
		}
	}
	best := uint64(0)
	for _, line := range strings.Split(lower, "\n") {
		line = strings.TrimSpace(line)
		if !strings.Contains(line, "packed") && !strings.Contains(line, "total size") {
			continue
		}
		fields := strings.Fields(line)
		for i, field := range fields {
			if !strings.Contains(field, ":") {
				continue
			}
			parts := strings.SplitN(field, ":", 2)
			key := strings.TrimSpace(parts[0])
			if key != "total" && key != "packed" && !strings.Contains(key, "packed") && !strings.Contains(key, "size") {
				continue
			}
			num := strings.TrimSpace(parts[1])
			if num == "" && i+1 < len(fields) {
				num = fields[i+1]
			}
			if parsed, ok := parseHumanSize(num + " " + strings.Join(fields[i+1:], " ")); ok && parsed > best {
				best = parsed
			}
		}
	}
	return best
}

func parseHumanSize(raw string) (uint64, bool) {
	fields := strings.Fields(strings.TrimSpace(raw))
	if len(fields) == 0 {
		return 0, false
	}
	numStr := strings.Trim(fields[0], ", ")
	value, err := strconv.ParseFloat(numStr, 64)
	if err != nil || value < 0 {
		return 0, false
	}
	unit := "B"
	if len(fields) > 1 {
		unit = strings.ToUpper(strings.TrimSpace(fields[1]))
	}
	multipliers := map[string]float64{
		"B": 1, "KB": 1024, "MB": 1048576, "GB": 1073741824, "TB": 1099511627776,
		"KIB": 1024, "MIB": 1048576, "GIB": 1073741824, "TIB": 1099511627776,
	}
	mult, ok := multipliers[unit]
	if !ok {
		mult = 1
	}
	return uint64(value * mult), true
}

func uint64Value(raw any) (uint64, bool) {
	switch value := raw.(type) {
	case uint64:
		return value, true
	case uint:
		return uint64(value), true
	case int:
		if value < 0 {
			return 0, false
		}
		return uint64(value), true
	case int64:
		if value < 0 {
			return 0, false
		}
		return uint64(value), true
	case float64:
		if value < 0 {
			return 0, false
		}
		return uint64(value), true
	case string:
		parsed, err := strconv.ParseUint(strings.TrimSpace(value), 10, 64)
		if err != nil {
			return 0, false
		}
		return parsed, true
	default:
		return 0, false
	}
}

func (e *Engine) runManagedBackup(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
) (string, map[string]any, string) {
	if p.Path == "" {
		return "failed", nil, "source_path is required"
	}
	boundary, boundaryErr := e.backupPathBoundary(p)
	if boundaryErr != nil {
		return "failed", agentPathBoundaryErrorResult(boundaryErr), boundaryErr.Error()
	}
	policySpec, policyParseErr := parseManagedBackupPolicy(p.Extra)
	if policyParseErr != nil {
		return "failed", map[string]any{
			"error_code":   "POLICY_APPLY_FAILED",
			"policy_phase": "validation",
		}, policyParseErr.Error()
	}
	if err := e.ensureNASMounted(ctx, p); err != nil {
		return "failed", nil, err.Error()
	}
	bin, err := e.kopiaBin(ctx)
	if err != nil {
		return "failed", nil, err.Error()
	}
	configFile, env, result, _, prepErr := e.prepareManagedRepository(ctx, rep, taskID, p, repositoryPrepareConnect)
	if prepErr != "" {
		return "failed", result, prepErr
	}
	if operationErr := attachManagedBackupOperation(result, p); operationErr != nil {
		result["error_code"] = "BACKUP_OPERATION_ID_INVALID"
		return "failed", result, operationErr.Error()
	}
	return runPreparedManagedBackup(ctx, rep, taskID, bin, configFile, env, p.Path, policySpec, result, boundary.patterns())
}

func (e *Engine) runManagedPolicyApply(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
) (string, map[string]any, string) {
	if p.Path == "" {
		return "failed", nil, "source_path is required"
	}
	boundary, boundaryErr := e.backupPathBoundary(p)
	if boundaryErr != nil {
		return "failed", agentPathBoundaryErrorResult(boundaryErr), boundaryErr.Error()
	}
	policySpec, err := parseManagedBackupPolicy(p.Extra)
	if err != nil {
		return "failed", map[string]any{"error_code": "POLICY_APPLY_FAILED", "policy_phase": "validation"}, err.Error()
	}
	if err := e.ensureNASMounted(ctx, p); err != nil {
		return "failed", nil, err.Error()
	}
	bin, err := e.kopiaBin(ctx)
	if err != nil {
		return "failed", nil, err.Error()
	}
	configFile, env, result, repository, prepErr := e.prepareManagedRepository(ctx, rep, taskID, p, repositoryPrepareConnect)
	if prepErr != "" {
		return "failed", result, prepErr
	}
	releaseSessionLock, sessionLockErr := repositorySessionLockFor(repository, configFile).acquireWrite(ctx)
	if sessionLockErr != nil {
		return "failed", result, sessionLockErr.Error()
	}
	defer releaseSessionLock()
	releasePathLock, lockErr := managedBackupPathLocks.acquire(ctx, configFile, p.Path)
	if lockErr != nil {
		return "failed", result, lockErr.Error()
	}
	defer releasePathLock()
	policyResult, policyErr := applyManagedBackupPolicy(ctx, bin, configFile, env, p.Path, policySpec, boundary.patterns())
	for key, value := range policyResult {
		result[key] = value
	}
	if policyErr != nil {
		return "failed", result, policyErr.Error()
	}
	return "success", result, ""
}

var (
	runManagedSnapshotCommand             = process.RunStreaming
	runManagedSnapshotStorageStatsCommand = process.RunStreamingDiscardStdout
)

var runManagedSnapshotReconcileCommand = func(
	ctx context.Context,
	bin string,
	args []string,
	env map[string]string,
	stdin string,
) (process.Result, error) {
	return runProcessWithTimeout(
		ctx,
		managedRepositoryKopiaCommandTimeout,
		bin,
		args,
		env,
		stdin,
	)
}

func managedBackupOperationID(p Payload) (string, error) {
	operationID := payloadStringValue(p.Extra["operation_id"])
	if operationID == "" {
		return "", nil
	}
	if len(operationID) > backupOperationIDMaxLength || !backupOperationIDPattern.MatchString(operationID) {
		return "", fmt.Errorf("invalid backup operation id")
	}
	return operationID, nil
}

func attachManagedBackupOperation(result map[string]any, p Payload) error {
	operationID, err := managedBackupOperationID(p)
	if err != nil {
		return err
	}
	if operationID == "" {
		return nil
	}
	result["operation_id"] = operationID
	if attempt, ok := payloadIntValue(p.Extra["operation_attempt"]); ok && attempt > 0 {
		result["operation_attempt"] = attempt
	}
	return nil
}

func (e *Engine) runManagedPreparedSnapshot(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
) (string, map[string]any, string) {
	if p.Path == "" {
		return "failed", nil, "source_path is required"
	}
	boundary, boundaryErr := e.backupPathBoundary(p)
	if boundaryErr != nil {
		return "failed", agentPathBoundaryErrorResult(boundaryErr), boundaryErr.Error()
	}
	if err := e.ensureNASMounted(ctx, p); err != nil {
		return "failed", nil, err.Error()
	}
	bin, err := e.kopiaBin(ctx)
	if err != nil {
		return "failed", nil, err.Error()
	}
	configFile, env, result, repository, prepErr := e.prepareManagedRepository(
		ctx,
		rep,
		taskID,
		p,
		repositoryPrepareConnect,
	)
	if prepErr != "" {
		return "failed", result, prepErr
	}
	if operationErr := attachManagedBackupOperation(result, p); operationErr != nil {
		result["error_code"] = "BACKUP_OPERATION_ID_INVALID"
		return "failed", result, operationErr.Error()
	}
	releaseSessionLock, sessionLockErr := repositorySessionLockFor(repository, configFile).acquireRead(ctx)
	if sessionLockErr != nil {
		return "failed", result, sessionLockErr.Error()
	}
	defer releaseSessionLock()
	releasePathLock, lockErr := managedBackupPathLocks.acquire(ctx, configFile, p.Path)
	if lockErr != nil {
		return "failed", result, lockErr.Error()
	}
	defer releasePathLock()
	verifyResult, verifyErr := verifyManagedBackupProtection(
		ctx,
		bin,
		configFile,
		env,
		p.Path,
		boundary.patterns(),
	)
	for key, value := range verifyResult {
		result[key] = value
	}
	if verifyErr != nil {
		return "failed", result, verifyErr.Error()
	}
	status, snapshotResult, errMsg := runPreparedManagedSnapshot(
		ctx, rep, taskID, bin, configFile, env, p.Path, result,
	)
	if status != "success" {
	}
	return status, snapshotResult, errMsg
}

func runPreparedManagedBackup(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	bin string,
	configFile string,
	env map[string]string,
	sourcePath string,
	policySpec managedBackupPolicySpec,
	result map[string]any,
	protectedPatterns ...[]string,
) (string, map[string]any, string) {
	releasePathLock, lockErr := managedBackupPathLocks.acquire(ctx, configFile, sourcePath)
	if lockErr != nil {
		return "failed", result, lockErr.Error()
	}
	defer releasePathLock()

	policyResult, policyErr := applyManagedBackupPolicy(ctx, bin, configFile, env, sourcePath, policySpec, protectedPatterns...)
	for key, value := range policyResult {
		result[key] = value
	}
	if policyErr != nil {
		return "failed", result, policyErr.Error()
	}
	return runPreparedManagedSnapshot(ctx, rep, taskID, bin, configFile, env, sourcePath, result)
}

func runPreparedManagedSnapshot(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	bin string,
	configFile string,
	env map[string]string,
	sourcePath string,
	result map[string]any,
) (string, map[string]any, string) {
	_ = sendProgress(ctx, rep, taskID, orchestrationProgressPayload(
		"snapshot_start",
		"Starting snapshot",
		map[string]any{"source_path": sourcePath},
	))

	operationID := payloadStringValue(result["operation_id"])
	operationAttempt, hasOperationAttempt := payloadIntValue(result["operation_attempt"])
	shouldReconcileOperation := operationID != "" && (!hasOperationAttempt || operationAttempt > 1)
	if shouldReconcileOperation {
		reconcileResult, existingSnapshot, matchCount, reconcileErr := findManagedSnapshotByOperation(
			ctx,
			bin,
			configFile,
			env,
			operationID,
		)
		result["snapshot_reconcile"] = commandResult(reconcileResult)
		if reconcileErr != nil {
			result["error_code"] = "KOPIA_SNAPSHOT_RECONCILE_FAILED"
			return "failed", result, reconcileErr.Error()
		}
		if existingSnapshot != nil {
			for key, value := range snapshotResultFromParsed(existingSnapshot) {
				result[key] = value
			}
			result["snapshot_reconciled"] = true
			result["snapshot_reconcile_match_count"] = matchCount
			_ = sendProgress(ctx, rep, taskID, map[string]any{
				"phase":             "kopia_transfer",
				"kopia_phase":       "snapshot_created",
				"kopia_percent":     100,
				"percent":           100,
				"bytes_done":        int64(1),
				"bytes_total":       int64(1),
				"bytes_total_known": true,
				"kopia_snapshot_id": stringValue(result["kopia_snapshot_id"]),
				"reconciled":        true,
			})
			return "success", result, ""
		}
	}

	snapshotArgs := managedBackupSnapshotArgs(configFile, sourcePath, operationID)
	progressState := newKopiaProgressReporter()
	failureCollector := newSnapshotFailureCollector()
	runCtx, cancelRun := context.WithCancel(ctx)
	stallSeconds := kopiaProgressStallSeconds()
	stallDone := make(chan struct{})
	go monitorKopiaProgressStall(runCtx, cancelRun, progressState, stallSeconds, stallDone)
	onProgressLine := func(line string, stderr bool) {
		failureCollector.observe(line)
		snapshot, ok := kopia.ParseProgressLine(line)
		if !ok {
			return
		}
		progressState.maybeSend(runCtx, rep, taskID, snapshot)
	}
	res, runErr := runManagedSnapshotCommand(runCtx, bin, snapshotArgs, env, "", onProgressLine)
	if runErr != nil && managedSnapshotStructuredProgressUnsupported(res) {
		res, runErr = runManagedSnapshotCommand(
			runCtx,
			bin,
			managedBackupLegacySnapshotArgs(configFile, sourcePath),
			env,
			"",
			onProgressLine,
		)
	}
	stalled := runCtx.Err() != nil && ctx.Err() == nil && progressState.stallExceeded(stallSeconds)
	close(stallDone)
	cancelRun()
	parsedResult := parseSnapshotOutput(res.Stdout)
	failureSummary, _ := parsedResult["snapshot_failure_summary"].(map[string]any)
	if failureSummary == nil {
		failureSummary = failureCollector.summary()
	}
	command := commandResult(res)
	if failureSummary != nil {
		// Full Kopia JSON and stderr repeat the same per-path failures and can be
		// hundreds of kilobytes. Preserve bounded tails for legacy diagnostics;
		// the stable summary below is the authoritative transport contract.
		delete(command, "stdout")
		delete(command, "stdout_tail")
		delete(command, "stderr")
		result["snapshot_failure_summary"] = failureSummary
		result["snapshot"] = compactSnapshotFailureProjection(failureSummary)
	}
	result["snapshot_create"] = command
	if len(parsedResult) > 0 {
		if failureSummary != nil {
			delete(parsedResult, "snapshot")
		}
		for key, value := range parsedResult {
			if key == "snapshot_failure_summary" && failureSummary != nil {
				continue
			}
			result[key] = value
		}
	}
	if runErr != nil {
		if stalled {
			result["error_code"] = "KOPIA_PROGRESS_STALL"
			return "failed", result, "kopia progress stall"
		}
		if managedSnapshotPolicyNotFound(res) {
			result["error_code"] = "KOPIA_POLICY_NOT_FOUND"
			result["policy_phase"] = "snapshot_create"
			return "failed", result, "kopia policy not found"
		}
		return "failed", result, runErr.Error()
	}
	collectManagedSnapshotStorageStats(ctx, bin, configFile, env, sourcePath, result)

	_ = sendProgress(ctx, rep, taskID, progressState.completionPayload(
		stringValue(result["kopia_snapshot_id"]),
	))
	return "success", result, ""
}

func collectManagedSnapshotStorageStats(
	ctx context.Context,
	bin string,
	configFile string,
	env map[string]string,
	sourcePath string,
	result map[string]any,
) {
	result["storage_stats_available"] = false
	snapshotID := strings.TrimSpace(stringValue(result["kopia_snapshot_id"]))
	if snapshotID == "" {
		result["storage_stats_error"] = "Kopia snapshot id is unavailable"
		return
	}

	var selected map[string]any
	onLine := func(line string, stderr bool) {
		if stderr || selected != nil {
			return
		}
		if metrics, ok := parseManagedSnapshotStorageStatsLine(line, snapshotID); ok {
			selected = metrics
		}
	}
	res, err := runManagedSnapshotStorageStatsCommand(
		ctx,
		bin,
		managedSnapshotStorageStatsArgs(configFile, sourcePath),
		env,
		"",
		onLine,
	)
	if err != nil {
		message := strings.TrimSpace(res.Stderr)
		if message == "" {
			message = err.Error()
		}
		result["storage_stats_error"] = truncateSnapshotBrowseError(message, 500)
		return
	}
	if selected == nil {
		result["storage_stats_error"] = "Created snapshot was not found in Kopia storage statistics"
		return
	}

	for key, value := range selected {
		result[key] = value
	}
	result["storage_stats_available"] = true
	result["storage_stats_captured_at"] = time.Now().UTC().Format(time.RFC3339Nano)
	result["stats"] = map[string]any{
		"size_bytes":                 selected["recoverable_size_bytes"],
		"recoverable_size_bytes":     selected["recoverable_size_bytes"],
		"file_count":                 selected["file_count"],
		"dir_count":                  selected["dir_count"],
		"symlink_count":              selected["symlink_count"],
		"new_original_content_bytes": selected["new_original_content_bytes"],
		"new_packed_content_bytes":   selected["new_packed_content_bytes"],
		"storage_stats_available":    true,
	}
}

func managedSnapshotPolicyNotFound(res process.Result) bool {
	output := strings.ToLower(res.Stderr + "\n" + res.Stdout)
	return strings.Contains(output, "unable to get policy tree") || strings.Contains(output, "policy not found")
}

func managedSnapshotStructuredProgressUnsupported(res process.Result) bool {
	output := strings.ToLower(res.Stderr + "\n" + res.Stdout)
	return strings.Contains(output, "progress-format") &&
		(strings.Contains(output, "unknown flag") || strings.Contains(output, "unknown long flag"))
}

func managedBackupSnapshotArgs(configFile string, sourcePath string, operationID string) []string {
	args := []string{
		"--config-file=" + configFile,
		"--progress",
		"--progress-format=hfl-json",
		"--progress-estimation-type=classic",
		"snapshot",
		"create",
		sourcePath,
	}
	if operationID != "" {
		args = append(args, "--tags="+backupOperationTagKey+":"+operationID)
	}
	return append(args, "--json")
}

func managedBackupLegacySnapshotArgs(configFile string, sourcePath string) []string {
	args := managedBackupSnapshotArgs(configFile, sourcePath, "")
	legacy := make([]string, 0, len(args)-1)
	for _, arg := range args {
		if arg != "--progress-format=hfl-json" {
			legacy = append(legacy, arg)
		}
	}
	return legacy
}

func managedSnapshotReconcileArgs(configFile string, operationID string) []string {
	return []string{
		"--config-file=" + configFile,
		"snapshot",
		"list",
		"--all",
		"--max-results=100",
		"--tags=" + backupOperationTagKey + ":" + operationID,
		"--json",
	}
}

func findManagedSnapshotByOperation(
	ctx context.Context,
	bin string,
	configFile string,
	env map[string]string,
	operationID string,
) (process.Result, map[string]any, int, error) {
	args := managedSnapshotReconcileArgs(configFile, operationID)
	res, runErr := runManagedSnapshotReconcileCommand(ctx, bin, args, env, "")
	if runErr != nil {
		return res, nil, 0, fmt.Errorf("snapshot reconciliation failed: %w", runErr)
	}
	parsed, ok := decodeJSONLoose(strings.TrimSpace(res.Stdout))
	if !ok {
		return res, nil, 0, fmt.Errorf("snapshot reconciliation returned invalid JSON")
	}
	rows, ok := parsed.([]any)
	if !ok {
		return res, nil, 0, fmt.Errorf("snapshot reconciliation returned an invalid result")
	}

	var selected map[string]any
	var selectedEnd time.Time
	matchCount := 0
	for _, raw := range rows {
		row, rowOK := raw.(map[string]any)
		if !rowOK || !managedSnapshotHasOperation(row, operationID) {
			continue
		}
		if findStringKey(row, "id", "snapshot_id", "snapshotID", "kopia_snapshot_id") == "" {
			return res, nil, matchCount, fmt.Errorf("snapshot reconciliation returned a snapshot without identity")
		}
		matchCount++
		endTime, _ := time.Parse(time.RFC3339Nano, payloadStringValue(row["endTime"]))
		if selected == nil || endTime.After(selectedEnd) {
			selected = row
			selectedEnd = endTime
		}
	}
	return res, selected, matchCount, nil
}

func managedSnapshotHasOperation(snapshot map[string]any, operationID string) bool {
	tags, ok := snapshot["tags"].(map[string]any)
	if !ok {
		return false
	}
	return payloadStringValue(tags["tag:"+backupOperationTagKey]) == operationID ||
		payloadStringValue(tags[backupOperationTagKey]) == operationID
}

func managedSnapshotStorageStatsArgs(configFile string, sourcePath string) []string {
	return []string{
		"--config-file=" + configFile,
		"snapshot",
		"list",
		"--storage-stats",
		"--no-retention",
		"--json",
		sourcePath,
	}
}

func parseManagedSnapshotStorageStatsLine(line string, snapshotID string) (map[string]any, bool) {
	trimmed := strings.TrimSpace(line)
	trimmed = strings.TrimSuffix(trimmed, ",")
	if trimmed == "" || trimmed == "[" || trimmed == "]" {
		return nil, false
	}

	var row map[string]any
	if json.Unmarshal([]byte(trimmed), &row) != nil {
		return nil, false
	}
	if strings.TrimSpace(stringValue(row["id"])) != strings.TrimSpace(snapshotID) {
		return nil, false
	}

	rootEntry, ok := row["rootEntry"].(map[string]any)
	if !ok {
		return nil, false
	}
	summary, ok := rootEntry["summ"].(map[string]any)
	if !ok {
		return nil, false
	}
	storageStats, ok := row["storageStats"].(map[string]any)
	if !ok {
		return nil, false
	}
	newData, ok := storageStats["newData"].(map[string]any)
	if !ok {
		return nil, false
	}

	recoverableSize, sizeOK := int64Value(summary["size"])
	fileCount, fileOK := int64Value(summary["files"])
	dirCount, dirOK := int64Value(summary["dirs"])
	symlinkCount, symlinkOK := int64Value(summary["symlinks"])
	newOriginalBytes, originalOK := int64Value(newData["originalContentBytes"])
	newPackedBytes, packedOK := int64Value(newData["packedContentBytes"])
	if !sizeOK || !fileOK || !dirOK || !originalOK || !packedOK ||
		recoverableSize < 0 || fileCount < 0 || dirCount < 0 ||
		newOriginalBytes < 0 || newPackedBytes < 0 {
		return nil, false
	}
	if !symlinkOK || symlinkCount < 0 {
		symlinkCount = 0
	}

	return map[string]any{
		"recoverable_size_bytes":     recoverableSize,
		"size_bytes":                 recoverableSize,
		"file_count":                 fileCount,
		"dir_count":                  dirCount,
		"symlink_count":              symlinkCount,
		"new_original_content_bytes": newOriginalBytes,
		"new_packed_content_bytes":   newPackedBytes,
	}, true
}

func (e *Engine) runManagedSnapshotDelete(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
) (string, map[string]any, string) {
	bin, err := e.kopiaBin(ctx)
	if err != nil {
		return "failed", nil, err.Error()
	}
	configFile, env, result, _, prepErr := e.prepareManagedRepository(ctx, rep, taskID, p, repositoryPrepareConnect)
	if prepErr != "" {
		return "failed", result, prepErr
	}
	ids := snapshotIDsFromPayload(p)
	if len(ids) == 0 {
		return "failed", result, "kopia_snapshot_ids is required"
	}
	results := make([]map[string]any, 0, len(ids))
	failed := 0
	for index, snapshotID := range ids {
		_ = sendProgress(ctx, rep, taskID, map[string]any{
			"phase":             "snapshot_delete",
			"kopia_snapshot_id": snapshotID,
			"index":             index + 1,
			"total":             len(ids),
			"percent":           int(float64(index) / float64(len(ids)) * 100),
		})
		args := managedSnapshotDeleteArgs(configFile, snapshotID)
		res, runErr := runProcessWithTimeout(ctx, managedRepositoryKopiaCommandTimeout, bin, args, env, "")
		item := map[string]any{
			"kopia_snapshot_id": snapshotID,
			"status":            "success",
			"delete":            commandResult(res),
		}
		if runErr != nil {
			failed++
			item["status"] = "failed"
			item["error_message"] = runErr.Error()
		}
		results = append(results, item)
	}
	result["results"] = results
	result["deleted_count"] = len(ids) - failed
	result["failed_count"] = failed
	_ = sendProgress(ctx, rep, taskID, map[string]any{
		"phase":         "snapshot_delete_finished",
		"deleted_count": len(ids) - failed,
		"failed_count":  failed,
		"percent":       100,
	})
	if failed > 0 {
		return "failed", result, fmt.Sprintf("%d snapshot delete operation(s) failed", failed)
	}
	return "success", result, ""
}

func snapshotIDsFromPayload(p Payload) []string {
	var rawItems []any
	if values, ok := p.Extra["kopia_snapshot_ids"].([]any); ok {
		rawItems = values
	} else if values, ok := p.Extra["snapshot_ids"].([]any); ok {
		rawItems = values
	}
	seen := map[string]bool{}
	ids := []string{}
	add := func(value string) {
		id := strings.TrimSpace(value)
		if id == "" || seen[id] {
			return
		}
		seen[id] = true
		ids = append(ids, id)
	}
	for _, item := range rawItems {
		add(stringValue(item))
	}
	add(p.SnapshotID)
	return ids
}

func managedSnapshotDeleteArgs(configFile string, snapshotID string) []string {
	return []string{
		"--config-file=" + configFile,
		"snapshot",
		"delete",
		snapshotID,
		"--delete",
	}
}

func snapshotObjectPath(snapshotID string, relPath string) string {
	snapshot := strings.TrimSpace(snapshotID)
	rel := strings.Trim(strings.TrimSpace(relPath), "/\\")
	if rel == "" {
		return snapshot
	}
	return snapshot + "/" + rel
}

func newInsightSnapshotResult(snapshotID string, path string) map[string]any {
	return map[string]any{
		"path":        strings.Trim(strings.TrimSpace(path), "/\\"),
		"snapshot_id": strings.TrimSpace(snapshotID),
	}
}

type snapshotScopeSelection struct {
	name        string
	found       bool
	pathType    string
	sizeBytes   int64
	modifiedAt  string
	invalidType bool
}

type insightSnapshotBrowseCollector struct {
	basePath            string
	limit               int
	entries             []map[string]any
	hasMore             bool
	invalid             bool
	skippedSpecialCount int64
}

func newInsightSnapshotBrowseCollector(basePath string, limit int) *insightSnapshotBrowseCollector {
	if limit <= 0 || limit > 500 {
		limit = 500
	}
	return &insightSnapshotBrowseCollector{
		basePath: strings.Trim(strings.TrimSpace(basePath), "/\\"),
		limit:    limit,
		entries:  make([]map[string]any, 0, limit),
	}
}

func (collector *insightSnapshotBrowseCollector) consume(line string) bool {
	if len(collector.entries) >= collector.limit {
		collector.hasMore = true
		return false
	}
	mode, size, modTime, name, ok := parseInsightSnapshotLongLine(line)
	if !ok {
		collector.invalid = true
		return false
	}
	pathType, valid := classifyInsightSnapshotEntry(mode, size)
	if !valid {
		collector.invalid = true
		return false
	}
	if pathType == "special" {
		collector.skippedSpecialCount++
		return true
	}
	isDir := pathType == "dir"
	path := normalizeSnapshotBrowsePath(name, name, collector.basePath, "")
	collector.entries = append(collector.entries, map[string]any{
		"name":         snapshotBrowseName(name, path),
		"path":         path,
		"type":         mapSnapshotBrowseType(isDir),
		"is_dir":       isDir,
		"size_bytes":   size,
		"modified_at":  modTime,
		"downloadable": true,
		"has_children": nil,
	})
	return true
}

func (selection *snapshotScopeSelection) inspectLine(line string) {
	if selection.found {
		return
	}
	mode, size, modifiedAt, name, ok := parseInsightSnapshotLongLine(line)
	if !ok || filepath.Base(filepath.ToSlash(name)) != selection.name {
		return
	}
	selection.found = true
	selection.modifiedAt = modifiedAt
	pathType, valid := classifyInsightSnapshotEntry(mode, size)
	if !valid {
		selection.invalidType = true
		return
	}
	switch pathType {
	case "file":
		selection.pathType = "file"
		selection.sizeBytes = size
	case "dir":
		selection.pathType = "dir"
	default:
		selection.invalidType = true
	}
}

func classifyInsightSnapshotEntry(mode string, size int64) (string, bool) {
	if size < 0 {
		return "", false
	}
	normalizedMode := strings.ToLower(strings.TrimSpace(mode))
	switch {
	case strings.HasPrefix(normalizedMode, "-"):
		return "file", true
	case strings.HasPrefix(normalizedMode, "d"):
		return "dir", true
	case normalizedMode != "":
		return "special", true
	default:
		return "", false
	}
}

func inspectManagedSnapshotSelection(
	ctx context.Context,
	bin string,
	configFile string,
	env map[string]string,
	snapshotID string,
	cleanPath string,
) (snapshotScopeSelection, process.Result, error) {
	parentPath := filepath.ToSlash(filepath.Dir(cleanPath))
	if parentPath == "." {
		parentPath = ""
	}
	selection := snapshotScopeSelection{
		name: filepath.Base(filepath.ToSlash(cleanPath)),
	}
	inspectCtx, cancelInspect := context.WithCancel(ctx)
	defer cancelInspect()
	inspectResult, inspectErr := process.RunStreamingDiscardStdout(
		inspectCtx,
		bin,
		[]string{
			"--config-file=" + configFile,
			"ls",
			"-l",
			snapshotObjectPath(snapshotID, parentPath),
		},
		env,
		"",
		func(line string, stderr bool) {
			if stderr {
				return
			}
			selection.inspectLine(line)
			if selection.found {
				cancelInspect()
			}
		},
	)
	if selection.found && ctx.Err() == nil {
		inspectErr = nil
	}
	return selection, inspectResult, inspectErr
}

func (e *Engine) runManagedSnapshotBrowse(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
) (string, map[string]any, string) {
	if p.SnapshotID == "" {
		return "failed", nil, "snapshot_id is required"
	}
	bin, err := e.kopiaBin(ctx)
	if err != nil {
		return "failed", nil, err.Error()
	}
	configFile, env, result, _, prepErr := e.prepareManagedRepository(ctx, rep, taskID, p, repositoryPrepareConnect)
	if prepErr != "" {
		return "failed", result, prepErr
	}
	target := snapshotObjectPath(p.SnapshotID, p.Path)
	args := []string{"--config-file=" + configFile, "ls", "--json", target}
	res, runErr := process.Run(ctx, bin, args, env, "")
	if runErr != nil && snapshotBrowseJsonUnsupported(res) {
		args = []string{"--config-file=" + configFile, "ls", "-l", target}
		res, runErr = process.Run(ctx, bin, args, env, "")
	}
	result["snapshot_browse"] = commandResult(res)
	result["path"] = strings.Trim(strings.TrimSpace(p.Path), "/\\")
	result["snapshot_id"] = p.SnapshotID
	entries := parseSnapshotBrowseOutput(res.Stdout, p.Path, p.SnapshotID)
	result["entries"] = entries
	result["count"] = len(entries)
	result["has_more"] = false
	if runErr != nil {
		return "failed", result, snapshotBrowseFailureMessage(res, runErr)
	}
	return "success", result, ""
}

func (e *Engine) runManagedInsightSnapshotBrowse(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
) (string, map[string]any, string) {
	if p.SnapshotID == "" {
		return "failed", nil, "snapshot_id is required"
	}
	bin, err := e.kopiaBin(ctx)
	if err != nil {
		return "failed", nil, err.Error()
	}
	configFile, env, prepareResult, _, prepErr := e.prepareManagedRepository(
		ctx,
		rep,
		taskID,
		p,
		repositoryPrepareConnect,
	)
	if prepErr != "" {
		if prepareResult != nil {
			return "failed", map[string]any{
				"error_code": insightRepositoryUnavailableErrorCode,
			}, "snapshot repository is unavailable"
		}
		return "failed", nil, prepErr
	}
	basePath := strings.Trim(strings.TrimSpace(p.Path), "/\\")
	result := newInsightSnapshotResult(p.SnapshotID, basePath)
	target := snapshotObjectPath(p.SnapshotID, basePath)
	args := []string{"--config-file=" + configFile, "ls", "-l", target}
	collector := newInsightSnapshotBrowseCollector(basePath, p.Limit)
	runCtx, cancelRun := context.WithCancel(ctx)
	defer cancelRun()
	res, runErr := process.RunStreamingDiscardStdout(
		runCtx,
		bin,
		args,
		env,
		"",
		func(line string, stderr bool) {
			if !stderr && !collector.consume(line) {
				cancelRun()
			}
		},
	)
	limitReached := collector.hasMore && ctx.Err() == nil
	if collector.invalid {
		return "failed", result, "snapshot browse returned invalid directory entries"
	}
	if runErr != nil && !limitReached {
		if basePath != "" {
			selection, _, inspectErr := inspectManagedSnapshotSelection(
				ctx,
				bin,
				configFile,
				env,
				p.SnapshotID,
				basePath,
			)
			if inspectErr == nil && selection.found && !selection.invalidType && selection.pathType == "file" {
				result["entries"] = []map[string]any{
					{
						"name":         selection.name,
						"path":         basePath,
						"type":         "file",
						"is_dir":       false,
						"size_bytes":   selection.sizeBytes,
						"modified_at":  selection.modifiedAt,
						"downloadable": true,
						"has_children": nil,
					},
				}
				result["count"] = 1
				result["has_more"] = false
				result["skipped_special_count"] = int64(0)
				return "success", result, ""
			}
			if inspectErr == nil && selection.found && selection.invalidType {
				result["error_code"] = insightUnsupportedContentErrorCode
				return "failed", result, "selected snapshot path type is not supported"
			}
			if inspectErr == nil && !selection.found {
				result["error_code"] = insightSnapshotPathNotFoundErrorCode
				return "failed", result, "selected snapshot path was not found"
			}
		}
		return "failed", result, snapshotBrowseFailureMessage(res, runErr)
	}
	result["entries"] = collector.entries
	result["count"] = len(collector.entries)
	result["has_more"] = collector.hasMore
	result["skipped_special_count"] = collector.skippedSpecialCount
	return "success", result, ""
}

func (e *Engine) runManagedSnapshotScopeResolve(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
) (string, map[string]any, string) {
	if p.SnapshotID == "" {
		return "failed", nil, "snapshot_id is required"
	}
	bin, err := e.kopiaBin(ctx)
	if err != nil {
		return "failed", nil, err.Error()
	}
	configFile, env, prepareResult, _, prepErr := e.prepareManagedRepository(
		ctx,
		rep,
		taskID,
		p,
		repositoryPrepareConnect,
	)
	if prepErr != "" {
		if prepareResult != nil {
			return "failed", map[string]any{
				"error_code": insightRepositoryUnavailableErrorCode,
			}, "snapshot repository is unavailable"
		}
		return "failed", nil, prepErr
	}
	cleanPath := strings.Trim(strings.TrimSpace(p.Path), "/\\")
	result := newInsightSnapshotResult(p.SnapshotID, cleanPath)
	pathType := "dir"
	var selectedFileSize int64
	if cleanPath == "" && strings.EqualFold(
		strings.TrimSpace(stringValue(p.Extra["root_path_type"])),
		"file",
	) {
		pathType = "file"
		var sizeOK bool
		selectedFileSize, sizeOK = exactInt64Value(p.Extra["root_size_bytes"])
		if !sizeOK || selectedFileSize < 0 {
			return "failed", result, "snapshot root file size is invalid"
		}
	} else if cleanPath != "" {
		selection, inspectResult, inspectErr := inspectManagedSnapshotSelection(
			ctx,
			bin,
			configFile,
			env,
			p.SnapshotID,
			cleanPath,
		)
		if inspectErr != nil {
			return "failed", result, snapshotBrowseFailureMessage(inspectResult, inspectErr)
		}
		if selection.invalidType {
			result["error_code"] = insightUnsupportedContentErrorCode
			return "failed", result, "selected snapshot path type is not supported"
		}
		if !selection.found {
			result["error_code"] = insightSnapshotPathNotFoundErrorCode
			return "failed", result, "selected snapshot path was not found"
		}
		pathType = selection.pathType
		selectedFileSize = selection.sizeBytes
	}
	result["path_type"] = pathType
	if pathType == "file" {
		result["file_count"] = int64(1)
		result["directory_count"] = int64(0)
		result["size_bytes"] = selectedFileSize
		result["skipped_special_count"] = int64(0)
		return "success", result, ""
	}

	target := snapshotObjectPath(p.SnapshotID, cleanPath)
	args := []string{"--config-file=" + configFile, "ls", "-lr", target}
	var fileCount int64
	var sizeBytes int64
	var directoryCount int64
	var skippedSpecialCount int64
	var invalidTotals bool
	res, runErr := process.RunStreamingDiscardStdout(
		ctx,
		bin,
		args,
		env,
		"",
		func(line string, stderr bool) {
			if stderr {
				return
			}
			mode, size, _, _, ok := parseInsightSnapshotLongLine(line)
			if !ok {
				if strings.TrimSpace(line) != "" {
					invalidTotals = true
				}
				return
			}
			pathType, valid := classifyInsightSnapshotEntry(mode, size)
			if !valid {
				invalidTotals = true
				return
			}
			if pathType == "dir" {
				directoryCount++
				return
			}
			if pathType == "special" {
				// Symlinks, sockets, pipes, devices and other special file
				// types are present in the snapshot but cannot be used as
				// chat source entries. Skip them instead of failing the whole
				// scope resolve.
				skippedSpecialCount++
				return
			}
			fileCount++
			if size > 0 && sizeBytes <= math.MaxInt64-size {
				sizeBytes += size
			} else if size > 0 {
				invalidTotals = true
			}
		},
	)
	if runErr != nil {
		return "failed", result, snapshotBrowseFailureMessage(res, runErr)
	}
	if invalidTotals {
		return "failed", result, "snapshot scope contains unsupported or invalid entries"
	}
	result["file_count"] = fileCount
	result["directory_count"] = directoryCount
	result["size_bytes"] = sizeBytes
	result["skipped_special_count"] = skippedSpecialCount
	return "success", result, ""
}

func (e *Engine) runManagedSnapshotDownload(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
) (string, map[string]any, string) {
	if p.SnapshotID == "" {
		return "failed", nil, "snapshot_id is required"
	}
	bin, err := e.kopiaBin(ctx)
	if err != nil {
		return "failed", nil, err.Error()
	}
	configFile, env, result, _, prepErr := e.prepareManagedRepository(ctx, rep, taskID, p, repositoryPrepareConnect)
	if prepErr != "" {
		return "failed", result, prepErr
	}
	tempDir, mkErr := os.MkdirTemp("", "hfl-kopia-download-*")
	if mkErr != nil {
		return "failed", result, mkErr.Error()
	}
	defer os.RemoveAll(tempDir)
	restoreRoot := filepath.Join(tempDir, "restore")
	if err := os.MkdirAll(restoreRoot, 0o700); err != nil {
		return "failed", result, err.Error()
	}
	requestedPaths, pathsErr := snapshotDownloadPaths(p)
	if pathsErr != nil {
		return "failed", result, pathsErr.Error()
	}
	batchDownload := len(requestedPaths) > 0
	forceZip := batchDownload
	if len(requestedPaths) == 0 {
		requestedPaths = []string{strings.Trim(strings.TrimSpace(p.Path), "/\\")}
	}
	var singleIsDir bool
	for _, requestedPath := range requestedPaths {
		target := snapshotObjectPath(p.SnapshotID, requestedPath)
		isDir, inspectRes, inspectErr := snapshotDownloadTargetIsDir(ctx, bin, configFile, env, target)
		result["snapshot_download_inspect"] = commandResult(inspectRes)
		if inspectErr != nil && !snapshotDownloadInspectNotDirectory(inspectRes) {
			return "failed", result, snapshotDownloadFailureMessage(inspectRes, inspectErr)
		}
		singleIsDir = isDir
		forceZip = forceZip || isDir
		restoreTarget := restoreRoot
		if batchDownload {
			restoreTarget = filepath.Join(restoreRoot, filepath.FromSlash(requestedPath))
			if err := os.MkdirAll(filepath.Dir(restoreTarget), 0o700); err != nil {
				return "failed", result, err.Error()
			}
		} else if !isDir {
			restoreTarget = filepath.Join(restoreRoot, snapshotDownloadFilename(requestedPath))
		}
		restoreArgs := []string{"--config-file=" + configFile, "restore", target, restoreTarget}
		res, runErr := process.Run(ctx, bin, restoreArgs, env, "")
		result["snapshot_download"] = commandResult(res)
		if runErr != nil {
			return "failed", result, snapshotDownloadFailureMessage(res, runErr)
		}
	}

	uploadSpec, hasUpload, uploadErr := parseSnapshotArtifactUpload(p.Extra["artifact_upload"])
	if uploadErr != nil {
		return "failed", result, uploadErr.Error()
	}
	artifactPath := ""
	filename := ""
	contentType := "application/octet-stream"
	if forceZip {
		filename = snapshotDownloadArchiveName(p.Path, batchDownload)
		artifactPath = filepath.Join(tempDir, filename)
		maxBytes := int64(0)
		if hasUpload {
			maxBytes = uploadSpec.MaxBytes
		}
		if err := zipDirectoryContentsToFile(restoreRoot, artifactPath, maxBytes); err != nil {
			return "failed", result, err.Error()
		}
		contentType = "application/zip"
	} else {
		entries, readErr := os.ReadDir(restoreRoot)
		if readErr != nil || len(entries) != 1 || entries[0].IsDir() || singleIsDir {
			return "failed", result, "restored file was not found"
		}
		artifactPath = filepath.Join(restoreRoot, entries[0].Name())
		filename = entries[0].Name()
	}
	info, statErr := os.Stat(artifactPath)
	if statErr != nil {
		return "failed", result, statErr.Error()
	}
	if hasUpload {
		uploadResult, uploadErr := e.uploadSnapshotArtifact(
			ctx, uploadSpec, artifactPath, filename, contentType,
		)
		if uploadErr != nil {
			return "failed", result, uploadErr.Error()
		}
		for key, value := range uploadResult {
			result[key] = value
		}
		result["snapshot_id"] = p.SnapshotID
		result["path"] = strings.Trim(strings.TrimSpace(p.Path), "/\\")
		return "success", result, ""
	}
	downloadContent, readErr := os.ReadFile(artifactPath)
	if readErr != nil {
		return "failed", result, readErr.Error()
	}
	result["snapshot_id"] = p.SnapshotID
	result["path"] = strings.Trim(strings.TrimSpace(p.Path), "/\\")
	result["filename"] = filename
	result["size_bytes"] = info.Size()
	result["content_type"] = contentType
	result["content_base64"] = base64.StdEncoding.EncodeToString(downloadContent)
	return "success", result, ""
}

type snapshotArtifactUploadSpec struct {
	ArtifactID int64
	Path       string
	Token      string
	MaxBytes   int64
}

func parseSnapshotArtifactUpload(raw any) (snapshotArtifactUploadSpec, bool, error) {
	data, ok := raw.(map[string]any)
	if !ok || len(data) == 0 {
		return snapshotArtifactUploadSpec{}, false, nil
	}
	artifactID, idOK := exactInt64Value(data["artifact_id"])
	maxBytes, maxOK := exactInt64Value(data["max_bytes"])
	spec := snapshotArtifactUploadSpec{
		ArtifactID: artifactID,
		Path:       strings.TrimSpace(stringValue(data["path"])),
		Token:      strings.TrimSpace(stringValue(data["token"])),
		MaxBytes:   maxBytes,
	}
	if !idOK || spec.ArtifactID <= 0 || !maxOK || spec.MaxBytes <= 0 || spec.Path == "" || spec.Token == "" {
		return snapshotArtifactUploadSpec{}, false, fmt.Errorf("snapshot artifact upload contract is invalid")
	}
	if !strings.HasPrefix(spec.Path, "/api/") || strings.Contains(spec.Path, "..") {
		return snapshotArtifactUploadSpec{}, false, fmt.Errorf("snapshot artifact upload path is invalid")
	}
	return spec, true, nil
}

func snapshotDownloadPaths(p Payload) ([]string, error) {
	raw, ok := p.Extra["paths"]
	if !ok || raw == nil {
		return nil, nil
	}
	items, ok := raw.([]any)
	if !ok {
		return nil, fmt.Errorf("snapshot download paths must be a list")
	}
	paths := make([]string, 0, len(items))
	seen := map[string]struct{}{}
	for _, item := range items {
		value := strings.TrimSpace(stringValue(item))
		value = strings.ReplaceAll(value, "\\", "/")
		if path.IsAbs(value) || filepath.VolumeName(value) != "" {
			return nil, fmt.Errorf("snapshot download path is invalid")
		}
		value = strings.Trim(value, "/")
		clean := path.Clean(value)
		if value == "" || clean == "." || clean == ".." || strings.HasPrefix(clean, "../") {
			return nil, fmt.Errorf("snapshot download path is invalid")
		}
		if _, exists := seen[clean]; exists {
			continue
		}
		seen[clean] = struct{}{}
		paths = append(paths, clean)
	}
	if len(paths) == 0 {
		return nil, fmt.Errorf("snapshot download paths are empty")
	}
	return paths, nil
}

func snapshotDownloadArchiveName(requestedPath string, batch bool) string {
	if batch {
		return "snapshot-download.zip"
	}
	name := snapshotDownloadFilename(requestedPath)
	if name == "download" {
		name = "snapshot"
	}
	return name + ".zip"
}

func fileSHA256(filePath string) (string, error) {
	file, err := os.Open(filePath)
	if err != nil {
		return "", err
	}
	defer file.Close()
	digest := sha256.New()
	if _, err := io.Copy(digest, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(digest.Sum(nil)), nil
}

func (e *Engine) uploadSnapshotArtifact(
	ctx context.Context,
	spec snapshotArtifactUploadSpec,
	filePath string,
	filename string,
	contentType string,
) (map[string]any, error) {
	info, err := os.Stat(filePath)
	if err != nil {
		return nil, err
	}
	if info.Size() > spec.MaxBytes {
		return nil, fmt.Errorf("snapshot download exceeds the configured size limit")
	}
	checksum, err := fileSHA256(filePath)
	if err != nil {
		return nil, err
	}
	base := strings.TrimRight(strings.TrimSpace(e.current().APIBaseURL), "/")
	if base == "" {
		return nil, fmt.Errorf("control plane API URL is not configured")
	}
	baseURL, err := url.Parse(base)
	if err != nil || (baseURL.Scheme != "https" && baseURL.Scheme != "http") || baseURL.Host == "" {
		return nil, fmt.Errorf("control plane API URL is invalid")
	}
	uploadURL := base + spec.Path
	client := &http.Client{}
	if tlsclient.InsecureTLSEnabled() {
		client.Transport = tlsclient.Transport()
	}
	var lastErr error
	for attempt := 1; attempt <= 2; attempt++ {
		file, openErr := os.Open(filePath)
		if openErr != nil {
			return nil, openErr
		}
		req, requestErr := http.NewRequestWithContext(ctx, http.MethodPut, uploadURL, file)
		if requestErr != nil {
			file.Close()
			return nil, requestErr
		}
		req.ContentLength = info.Size()
		req.Header.Set("Authorization", "Bearer "+spec.Token)
		req.Header.Set("Content-Type", contentType)
		req.Header.Set("X-Content-SHA256", checksum)
		req.Header.Set("X-Artifact-Filename", url.QueryEscape(filename))
		resp, sendErr := client.Do(req)
		file.Close()
		if sendErr != nil {
			lastErr = sendErr
			continue
		}
		body, readErr := io.ReadAll(io.LimitReader(resp.Body, 8*1024))
		resp.Body.Close()
		if readErr != nil {
			lastErr = readErr
			continue
		}
		if resp.StatusCode >= 200 && resp.StatusCode < 300 {
			return map[string]any{
				"artifact_id":  spec.ArtifactID,
				"filename":     filename,
				"content_type": contentType,
				"size_bytes":   info.Size(),
				"sha256":       checksum,
			}, nil
		}
		lastErr = fmt.Errorf("snapshot artifact upload returned HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(body)))
		if resp.StatusCode == http.StatusUnauthorized ||
			resp.StatusCode == http.StatusForbidden ||
			resp.StatusCode == http.StatusNotFound ||
			resp.StatusCode == http.StatusRequestEntityTooLarge {
			break
		}
	}
	return nil, fmt.Errorf("snapshot artifact upload failed: %w", lastErr)
}

type boundedArtifactWriter struct {
	w        io.Writer
	written  int64
	maxBytes int64
}

func (w *boundedArtifactWriter) Write(p []byte) (int, error) {
	if w.maxBytes > 0 && w.written+int64(len(p)) > w.maxBytes {
		return 0, fmt.Errorf("snapshot download exceeds the configured size limit")
	}
	n, err := w.w.Write(p)
	w.written += int64(n)
	return n, err
}

func zipDirectoryContentsToFile(root string, destination string, maxBytes int64) error {
	file, err := os.OpenFile(destination, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	bounded := &boundedArtifactWriter{w: file, maxBytes: maxBytes}
	zw := zip.NewWriter(bounded)
	walkErr := filepath.WalkDir(root, func(filePath string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if filePath == root {
			return nil
		}
		relative, err := filepath.Rel(root, filePath)
		if err != nil {
			return err
		}
		relative = filepath.ToSlash(relative)
		if entry.IsDir() {
			_, err = zw.Create(relative + "/")
			return err
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() {
			return nil
		}
		header, err := zip.FileInfoHeader(info)
		if err != nil {
			return err
		}
		header.Name = relative
		header.Method = zip.Deflate
		writer, err := zw.CreateHeader(header)
		if err != nil {
			return err
		}
		input, err := os.Open(filePath)
		if err != nil {
			return err
		}
		_, copyErr := io.Copy(writer, input)
		closeErr := input.Close()
		if copyErr != nil {
			return copyErr
		}
		return closeErr
	})
	closeZipErr := zw.Close()
	closeFileErr := file.Close()
	if walkErr != nil {
		os.Remove(destination)
		return walkErr
	}
	if closeZipErr != nil {
		os.Remove(destination)
		return closeZipErr
	}
	if closeFileErr != nil {
		os.Remove(destination)
		return closeFileErr
	}
	return nil
}

func snapshotDownloadTargetIsDir(ctx context.Context, bin string, configFile string, env map[string]string, target string) (bool, process.Result, error) {
	args := []string{"--config-file=" + configFile, "ls", "-l", target}
	res, err := process.Run(ctx, bin, args, env, "")
	if err != nil && snapshotDownloadInspectNotDirectory(res) {
		return false, res, nil
	}
	return err == nil, res, err
}

func snapshotDownloadInspectNotDirectory(res process.Result) bool {
	output := strings.ToLower(res.Stdout + "\n" + res.Stderr)
	return strings.Contains(output, "is not a directory object")
}

func snapshotDownloadFailureMessage(res process.Result, runErr error) string {
	stderr := strings.TrimSpace(res.Stderr)
	stdout := strings.TrimSpace(res.Stdout)
	switch {
	case stderr != "":
		return truncateSnapshotBrowseError("Snapshot download failed: "+stderr, 2000)
	case stdout != "":
		return truncateSnapshotBrowseError("Snapshot download failed: "+stdout, 2000)
	case runErr != nil:
		return runErr.Error()
	default:
		return "Snapshot download failed"
	}
}

func (e *Engine) runManagedRestore(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	p Payload,
) (string, map[string]any, string) {
	if p.SnapshotID == "" {
		return "failed", nil, "snapshot_id is required"
	}
	insightContentPolicy := strings.ToLower(
		strings.TrimSpace(stringValue(p.Extra["insight_content_policy"])),
	)
	if insightContentPolicy != "" && insightContentPolicy != insightRegularFilesOnlyPolicy {
		return "failed", nil, "insight content policy is not supported"
	}
	if insightContentPolicy != "" && payloadStringValue(p.Extra["managed_workspace_path"]) == "" {
		return "failed", nil, "insight content policy requires a managed workspace"
	}
	targetPath := restoreTargetPath(p)
	if targetPath == "" {
		return "failed", nil, "target_path is required"
	}
	if err := e.ensureNASMounted(ctx, p); err != nil {
		return "failed", nil, err.Error()
	}
	targetPath, nasMountRoot, err := resolveNASRestoreTarget(p, targetPath)
	if err != nil {
		return "failed", nil, err.Error()
	}
	if err := validateNASRestoreTarget(p, targetPath); err != nil {
		return "failed", nil, err.Error()
	}
	if err := validateLensManagedRestoreTarget(p, targetPath); err != nil {
		return "failed", nil, err.Error()
	}
	bin, err := e.kopiaBin(ctx)
	if err != nil {
		return "failed", nil, err.Error()
	}
	configFile, env, result, spec, prepErr := e.prepareManagedRepository(ctx, rep, taskID, p, repositoryPrepareConnect)
	if prepErr != "" {
		return "failed", managedRestoreResult(result, spec, p), managedRestorePreparationFailureMessage(result, prepErr, spec, p)
	}
	result = managedRestoreResult(result, spec, p)
	selectedPaths := restoreSelectedPaths(p)
	if len(selectedPaths) == 0 {
		selectedPaths = []string{""}
	}
	restored := make([]map[string]any, 0, len(selectedPaths))
	var completedBytes int64
	var skippedSpecialCount int64
	progressState := newKopiaProgressReporter()
	runCtx, cancelRun := context.WithCancel(ctx)
	defer cancelRun()
	stallSeconds := kopiaProgressStallSeconds()
	stallDone := make(chan struct{})
	go monitorKopiaProgressStall(runCtx, cancelRun, progressState, stallSeconds, stallDone)
	defer close(stallDone)

	for pathIndex, selectedPath := range selectedPaths {
		source := snapshotObjectPath(p.SnapshotID, selectedPath)
		restoreTarget := restoreTargetPathForSelection(p, targetPath, selectedPath)
		if err := validateNASRestoreTarget(p, restoreTarget); err != nil {
			return "failed", result, err.Error()
		}
		if err := validateLensManagedRestoreTarget(p, restoreTarget); err != nil {
			return "failed", result, err.Error()
		}
		sourceIsDir, inspectRes, inspectErr := snapshotDownloadTargetIsDir(ctx, bin, configFile, env, source)
		sourceObjectType := "directory"
		if inspectErr != nil {
			sourceObjectType = "unknown"
		} else if !sourceIsDir {
			sourceObjectType = "file"
		}
		restoreEntry := map[string]any{
			"snapshot_path":      source,
			"target_path":        restoreTarget,
			"source_is_dir":      sourceIsDir,
			"source_object_type": sourceObjectType,
			"snapshot_inspect":   managedRestoreCommandResult(inspectRes, spec, p),
		}
		if inspectErr != nil {
			restored = append(restored, restoreEntry)
			result["restore_results"] = restored
			result["restore_inspect"] = managedRestoreCommandResult(inspectRes, spec, p)
			return "failed", result, snapshotRestoreInspectFailureMessage(inspectRes, inspectErr, spec, p)
		}
		preparePath := restorePrepareTargetPathForSelection(p, targetPath, len(selectedPaths), sourceIsDir)
		restoreEntry["prepare_path"] = preparePath
		cleanupFileTargetDir := restoreTargetPathSemantics(p) == "final" && len(selectedPaths) == 1 && !sourceIsDir
		if mkErr := prepareRestoreTargetPath(
			preparePath,
			restoreTarget,
			cleanupFileTargetDir,
			nasMountRoot,
		); mkErr != nil {
			restoreEntry["prepare_error"] = mkErr.Error()
			restored = append(restored, restoreEntry)
			result["restore_results"] = restored
			return "failed", result, mkErr.Error()
		}
		pathOffset := completedBytes
		pathIndexValue := pathIndex + 1
		pathTotal := len(selectedPaths)
		var lastPathTotal int64
		onProgressLine := func(line string, _ bool) {
			snapshot, ok := kopia.ParseRestoreProgressLine(line)
			if !ok {
				return
			}
			if snapshot.TotalBytes > 0 {
				lastPathTotal = snapshot.TotalBytes
			}
			payload := kopia.RestoreProgressPayload(snapshot)
			payload["path_index"] = pathIndexValue
			payload["path_total"] = pathTotal
			if pathOffset > 0 {
				if done, ok := payload["bytes_done"].(int64); ok {
					payload["bytes_done"] = done + pathOffset
				}
				if total, ok := payload["bytes_total"].(int64); ok && total > 0 {
					payload["bytes_total"] = total + pathOffset
				}
			}
			progressState.maybeSendRestore(runCtx, rep, taskID, payload)
		}
		restoreArgs := []string{
			"--config-file=" + configFile,
			"--progress",
			"restore",
			source,
			restoreTarget,
		}
		res, runErr := process.RunStreaming(runCtx, bin, restoreArgs, env, "", onProgressLine)
		restoreEntry["result"] = managedRestoreCommandResult(res, spec, p)
		restored = append(restored, restoreEntry)
		if runErr != nil {
			if runCtx.Err() != nil && ctx.Err() == nil && progressState.stallExceeded(stallSeconds) {
				result["restore_results"] = restored
				return "failed", result, "kopia progress stall"
			}
			result["restore_results"] = restored
			result["restore"] = managedRestoreCommandResult(res, spec, p)
			return "failed", result, managedRestoreFailureMessage(res, runErr, spec, p)
		}
		if insightContentPolicy == insightRegularFilesOnlyPolicy {
			skipped, contentErr := enforceInsightRestoreContent(restoreTarget)
			if contentErr != nil {
				result["restore_results"] = restored
				return "failed", result, "failed to enforce Insight content policy: " + contentErr.Error()
			}
			skippedSpecialCount += skipped
			restoreEntry["skipped_special_count"] = skipped
		}
		if lastPathTotal > 0 {
			completedBytes += lastPathTotal
		}
	}
	if err := validateLensManagedRestoreTarget(p, targetPath); err != nil {
		return "failed", result, "managed workspace identity changed during restore: " + err.Error()
	}
	result["snapshot_id"] = p.SnapshotID
	result["target_path"] = targetPath
	result["selected_paths"] = selectedPaths
	result["restore_results"] = restored
	result["count"] = len(restored)
	if insightContentPolicy == insightRegularFilesOnlyPolicy {
		result["insight_content_policy"] = insightRegularFilesOnlyPolicy
		result["skipped_special_count"] = skippedSpecialCount
	}
	_ = sendProgress(ctx, rep, taskID, map[string]any{
		"phase":             "kopia_transfer",
		"kopia_phase":       "restore_completed",
		"kopia_percent":     100,
		"percent":           100,
		"bytes_done":        maxInt64(completedBytes, 1),
		"bytes_total":       maxInt64(completedBytes, 1),
		"bytes_total_known": true,
	})
	return "success", result, ""
}

func orchestrationProgressPayload(phase string, label string, extra map[string]any) map[string]any {
	payload := map[string]any{
		"phase":               "orchestration",
		"orchestration_phase": phase,
		"orchestration_label": label,
		"kopia_phase":         phase,
	}
	for key, value := range extra {
		payload[key] = value
	}
	return payload
}

func maxInt64(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}

func restoreTargetPath(p Payload) string {
	if value, ok := p.Extra["target_path"].(string); ok && strings.TrimSpace(value) != "" {
		return strings.TrimSpace(value)
	}
	return strings.TrimSpace(p.Path)
}

func snapshotRestoreInspectFailureMessage(
	res process.Result,
	runErr error,
	spec repositorySpec,
	p Payload,
) string {
	message := ""
	for _, output := range []string{res.Stderr, res.Stdout} {
		if trimmed := strings.TrimSpace(output); trimmed != "" {
			message = tailLines(trimmed, 20)
			break
		}
	}
	if message == "" && runErr != nil {
		message = runErr.Error()
	}
	if message == "" {
		message = "Kopia snapshot inspection failed"
	}
	message = redactManagedRestoreSecrets(message, spec, p)
	return truncateFailureTail("Snapshot restore inspect failed: ", message, 2000)
}

func managedRestoreFailureMessage(
	res process.Result,
	runErr error,
	spec repositorySpec,
	p Payload,
) string {
	message := ""
	for _, output := range []string{res.Stderr, res.Stdout} {
		if trimmed := strings.TrimSpace(output); trimmed != "" {
			message = tailLines(trimmed, 20)
			break
		}
	}
	if message == "" && runErr != nil {
		message = runErr.Error()
	}
	if message == "" {
		message = "Kopia restore command failed"
	}
	message = redactManagedRestoreSecrets(message, spec, p)
	const prefix = "Restore failed: "
	return truncateFailureTail(prefix, message, 2000)
}

func managedRestoreCommandResult(
	res process.Result,
	spec repositorySpec,
	p Payload,
) map[string]any {
	result := commandResult(res)
	for _, key := range []string{"stdout", "stderr", "stdout_tail", "stderr_tail"} {
		if value, ok := result[key].(string); ok {
			result[key] = redactManagedRestoreSecrets(value, spec, p)
		}
	}
	return result
}

func managedRestorePreparationFailureMessage(
	result map[string]any,
	fallback string,
	spec repositorySpec,
	p Payload,
) string {
	for _, key := range []string{"repository_status", "repository_connect", "repository_create"} {
		command, ok := result[key].(map[string]any)
		if !ok {
			continue
		}
		for _, outputKey := range []string{"stderr", "stdout"} {
			if output := strings.TrimSpace(stringValue(command[outputKey])); output != "" {
				message := redactManagedRestoreSecrets(tailLines(output, 20), spec, p)
				return truncateFailureTail("Restore repository preparation failed: ", message, 2000)
			}
		}
	}
	message := redactManagedRestoreSecrets(strings.TrimSpace(fallback), spec, p)
	if message == "" {
		message = "repository preparation failed"
	}
	return truncateFailureTail("Restore repository preparation failed: ", message, 2000)
}

func managedRestoreResult(
	result map[string]any,
	spec repositorySpec,
	p Payload,
) map[string]any {
	if result == nil {
		return nil
	}
	redacted, _ := redactManagedRestoreValue(result, spec, p).(map[string]any)
	return redacted
}

func redactManagedRestoreValue(value any, spec repositorySpec, p Payload) any {
	switch typed := value.(type) {
	case string:
		return redactManagedRestoreSecrets(typed, spec, p)
	case map[string]any:
		result := make(map[string]any, len(typed))
		for key, item := range typed {
			if restoreSecretKey(key) {
				result[key] = redactManagedRestoreSecretValue(item)
			} else {
				result[key] = redactManagedRestoreValue(item, spec, p)
			}
		}
		return result
	case []any:
		result := make([]any, len(typed))
		for index, item := range typed {
			result[index] = redactManagedRestoreValue(item, spec, p)
		}
		return result
	case []map[string]any:
		result := make([]map[string]any, len(typed))
		for index, item := range typed {
			result[index], _ = redactManagedRestoreValue(item, spec, p).(map[string]any)
		}
		return result
	default:
		return value
	}
}

func redactManagedRestoreSecretValue(value any) any {
	switch typed := value.(type) {
	case nil:
		return nil
	case string:
		if typed == "" {
			return typed
		}
		return "<redacted>"
	case map[string]any:
		result := make(map[string]any, len(typed))
		for key, item := range typed {
			result[key] = redactManagedRestoreSecretValue(item)
		}
		return result
	case []any:
		result := make([]any, len(typed))
		for index, item := range typed {
			result[index] = redactManagedRestoreSecretValue(item)
		}
		return result
	case []map[string]any:
		result := make([]map[string]any, len(typed))
		for index, item := range typed {
			redacted, _ := redactManagedRestoreSecretValue(item).(map[string]any)
			result[index] = redacted
		}
		return result
	default:
		return "<redacted>"
	}
}

func redactManagedRestoreSecrets(
	message string,
	spec repositorySpec,
	p Payload,
) string {
	secretSet := make(map[string]struct{})
	secretsToRedact := []string{
		spec.SecretAccessKey,
		spec.KopiaPassword,
		spec.ServerPassword,
	}
	if spec.TargetNAS != nil {
		secretsToRedact = append(secretsToRedact, spec.TargetNAS.Password)
	}
	if targetNAS, ok, err := parseNASSpec(p.Extra["nas"]); err == nil && ok {
		secretsToRedact = append(secretsToRedact, targetNAS.Password)
	}
	for key, value := range p.Env {
		if restoreSecretKey(key) {
			secretsToRedact = append(secretsToRedact, value)
		}
	}
	collectManagedRestorePayloadSecrets(p.Extra, &secretsToRedact)
	for _, secret := range secretsToRedact {
		if value := strings.TrimSpace(secret); value != "" {
			secretSet[value] = struct{}{}
		}
	}
	secrets := make([]string, 0, len(secretSet))
	for secret := range secretSet {
		secrets = append(secrets, secret)
	}
	sort.Slice(secrets, func(i, j int) bool {
		return len(secrets[i]) > len(secrets[j])
	})
	for _, secret := range secrets {
		message = strings.ReplaceAll(message, secret, "<redacted>")
	}
	return message
}

func collectManagedRestorePayloadSecrets(value any, secrets *[]string) {
	switch typed := value.(type) {
	case map[string]any:
		for key, item := range typed {
			if restoreSecretKey(key) {
				collectManagedRestoreSecretValues(item, secrets)
				continue
			}
			collectManagedRestorePayloadSecrets(item, secrets)
		}
	case []any:
		for _, item := range typed {
			collectManagedRestorePayloadSecrets(item, secrets)
		}
	case []map[string]any:
		for _, item := range typed {
			collectManagedRestorePayloadSecrets(item, secrets)
		}
	}
}

func collectManagedRestoreSecretValues(value any, secrets *[]string) {
	switch typed := value.(type) {
	case string:
		*secrets = append(*secrets, typed)
	case map[string]any:
		for _, item := range typed {
			collectManagedRestoreSecretValues(item, secrets)
		}
	case []any:
		for _, item := range typed {
			collectManagedRestoreSecretValues(item, secrets)
		}
	case []map[string]any:
		for _, item := range typed {
			collectManagedRestoreSecretValues(item, secrets)
		}
	}
}

func restoreSecretKey(key string) bool {
	normalized := strings.ToLower(strings.TrimSpace(key))
	return strings.Contains(normalized, "password") ||
		strings.Contains(normalized, "secret") ||
		strings.Contains(normalized, "token")
}

func truncateFailureTail(prefix string, message string, limit int) string {
	value := prefix + strings.TrimSpace(message)
	if limit <= 0 || len(value) <= limit {
		return value
	}
	remaining := limit - len(prefix) - len("...")
	if remaining <= 0 {
		return value[:limit]
	}
	start := len(value) - remaining
	for start < len(value) && !utf8.RuneStart(value[start]) {
		start++
	}
	return prefix + "..." + value[start:]
}

func restoreTargetPathSemantics(p Payload) string {
	return strings.ToLower(strings.TrimSpace(stringValue(p.Extra["target_path_semantics"])))
}

func restoreSelectedPaths(p Payload) []string {
	raw, ok := p.Extra["selected_paths"].([]any)
	if !ok || len(raw) == 0 {
		return []string{""}
	}
	paths := make([]string, 0, len(raw))
	for _, item := range raw {
		value := strings.Trim(strings.TrimSpace(fmt.Sprint(item)), "/\\")
		if value == "" || value == "." {
			continue
		}
		paths = append(paths, filepath.ToSlash(value))
	}
	if len(paths) == 0 {
		return []string{""}
	}
	return paths
}

func restoreTargetPathForSelection(p Payload, targetPath string, selectedPath string) string {
	if restoreTargetPathSemantics(p) == "final" {
		return targetPath
	}
	sourcePathType := strings.ToLower(strings.TrimSpace(stringValue(p.Extra["source_path_type"])))
	if sourcePathType == "" {
		sourcePathType = strings.ToLower(strings.TrimSpace(stringValue(p.Extra["path_type"])))
	}
	if sourcePathType != "file" && sourcePathType != "directory" {
		return targetPath
	}
	if strings.TrimSpace(selectedPath) != "" {
		return targetPath
	}
	sourceName := filepath.Base(strings.TrimSpace(stringValue(p.Extra["source_path"])))
	if sourceName == "" || sourceName == "." || sourceName == string(filepath.Separator) {
		return targetPath
	}
	return filepath.Join(targetPath, sourceName)
}

func restorePrepareTargetPath(p Payload, targetPath string, selectedPaths []string) string {
	if restoreTargetPathIsFinalSingleSelectedPath(p, selectedPaths) {
		return restoreTargetParentPath(targetPath)
	}
	if restoreTargetPathIsFinalFile(p, selectedPaths) {
		return restoreTargetParentPath(targetPath)
	}
	return targetPath
}

func restorePrepareTargetPathForSelection(
	p Payload,
	targetPath string,
	selectedPathCount int,
	sourceIsDir bool,
) string {
	if restoreTargetPathSemantics(p) != "final" {
		return targetPath
	}
	if selectedPathCount != 1 {
		return targetPath
	}
	if !sourceIsDir {
		return restoreTargetParentPath(targetPath)
	}
	return targetPath
}

func restoreTargetParentPath(targetPath string) string {
	parent := filepath.Dir(targetPath)
	if parent == "" {
		return "."
	}
	return parent
}

func prepareRestoreTargetPath(
	preparePath string,
	restoreTarget string,
	cleanupFileTargetDir bool,
	allowedRoot string,
) error {
	if cleanupFileTargetDir {
		if err := removeEmptyRestoreTargetDirectory(restoreTarget); err != nil {
			return err
		}
	}
	if strings.TrimSpace(allowedRoot) != "" {
		allowedRoot = filepath.Clean(strings.TrimSpace(allowedRoot))
		if filepath.Clean(preparePath) == allowedRoot {
			return nil
		}
		if _, _, err := secureEnsureDirectory(preparePath, allowedRoot, 0o755); err != nil {
			return err
		}
		return nil
	}
	if err := os.MkdirAll(preparePath, 0o755); err != nil {
		return err
	}
	return nil
}

func removeEmptyRestoreTargetDirectory(targetPath string) error {
	info, err := os.Stat(targetPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return fmt.Errorf("inspect restore target %q: %w", targetPath, err)
	}
	if !info.IsDir() {
		return nil
	}
	entries, err := os.ReadDir(targetPath)
	if err != nil {
		return fmt.Errorf("inspect restore target directory %q: %w", targetPath, err)
	}
	if len(entries) > 0 {
		return fmt.Errorf("target path %q is a non-empty directory; cannot restore a file to that path", targetPath)
	}
	if err := os.Remove(targetPath); err != nil {
		return fmt.Errorf("remove empty target directory %q: %w", targetPath, err)
	}
	return nil
}

func restoreTargetPathIsFinalSingleSelectedPath(p Payload, selectedPaths []string) bool {
	return restoreTargetPathSemantics(p) == "final" && len(selectedPaths) == 1 && strings.TrimSpace(selectedPaths[0]) != ""
}

func restoreTargetPathIsFinalFile(p Payload, selectedPaths []string) bool {
	if restoreTargetPathSemantics(p) != "final" {
		return false
	}
	if len(selectedPaths) != 1 || strings.TrimSpace(selectedPaths[0]) != "" {
		return false
	}
	sourcePathType := strings.ToLower(strings.TrimSpace(stringValue(p.Extra["source_path_type"])))
	if sourcePathType == "" {
		sourcePathType = strings.ToLower(strings.TrimSpace(stringValue(p.Extra["path_type"])))
	}
	return sourcePathType == "file"
}

func collectRestoredDownload(root string, requestedPath string, forceZip bool) ([]byte, string, string, error) {
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil, "", "", err
	}
	if len(entries) == 0 {
		return nil, "", "", fmt.Errorf("restored path not found")
	}
	if !forceZip && len(entries) == 1 && !entries[0].IsDir() {
		filePath := filepath.Join(root, entries[0].Name())
		file, openErr := os.Open(filePath)
		if openErr != nil {
			return nil, "", "", openErr
		}
		defer file.Close()
		content, readErr := io.ReadAll(file)
		if readErr != nil {
			return nil, "", "", readErr
		}
		return content, filepath.Base(filePath), "application/octet-stream", nil
	}
	content, zipErr := zipDirectoryContents(root)
	if zipErr != nil {
		return nil, "", "", zipErr
	}
	name := filepath.Base(strings.Trim(strings.TrimSpace(requestedPath), "/\\"))
	if name == "" || name == "." {
		name = "snapshot"
	}
	return content, name + ".zip", "application/zip", nil
}

func snapshotDownloadFilename(requestedPath string) string {
	name := filepath.Base(strings.Trim(strings.TrimSpace(requestedPath), "/\\"))
	if name == "" || name == "." {
		return "download"
	}
	return name
}

func zipDirectoryContents(root string) ([]byte, error) {
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if path == root {
			return nil
		}
		rel, relErr := filepath.Rel(root, path)
		if relErr != nil {
			return relErr
		}
		rel = filepath.ToSlash(rel)
		if d.IsDir() {
			_, createErr := zw.Create(rel + "/")
			return createErr
		}
		if d.Type()&os.ModeSymlink != 0 {
			return nil
		}
		info, infoErr := d.Info()
		if infoErr != nil {
			return infoErr
		}
		if !info.Mode().IsRegular() {
			return nil
		}
		header, headerErr := zip.FileInfoHeader(info)
		if headerErr != nil {
			return headerErr
		}
		header.Name = rel
		header.Method = zip.Deflate
		writer, createErr := zw.CreateHeader(header)
		if createErr != nil {
			return createErr
		}
		file, openErr := os.Open(path)
		if openErr != nil {
			return openErr
		}
		defer file.Close()
		_, copyErr := io.Copy(writer, file)
		return copyErr
	})
	if err != nil {
		_ = zw.Close()
		return nil, err
	}
	if err := zw.Close(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

func repositoryArgs(configFile string, spec repositorySpec, create bool) []string {
	action := "connect"
	if create {
		action = "create"
	}
	args := []string{
		"--config-file=" + configFile,
		"--no-persist-credentials",
		"repository",
		action,
	}
	switch spec.Type {
	case "s3":
		args = append(args, "s3", "--bucket="+spec.Bucket)
		if spec.Endpoint != "" {
			args = append(args, "--endpoint="+spec.Endpoint)
		}
		if spec.Region != "" {
			args = append(args, "--region="+spec.Region)
		}
		if spec.Prefix != "" {
			args = append(args, "--prefix="+spec.Prefix)
		}
		if !spec.UseTLS {
			args = append(args, "--disable-tls")
		}
		if spec.S3URLStyleFlag {
			style := spec.S3URLStyle
			if style == "virtual_hosted" {
				style = "virtual-hosted"
			}
			args = append(args, "--url-style="+style)
		}
	case "proxy_fs":
		args = append(args, "filesystem", "--path="+spec.Path)
	case "nas":
		args = append(args, "filesystem", "--path="+spec.Path)
	case "kopia_server":
		args = append(args, "server", "--url="+spec.ServerURL)
		if spec.ServerCert != "" {
			args = append(args, "--server-cert-fingerprint="+spec.ServerCert)
		}
		if user, host := kopiaServerIdentity(spec.ServerUsername); user != "" && host != "" {
			args = append(args, "--override-username="+user, "--override-hostname="+host)
		}
	}
	return args
}

func kopiaServerIdentity(username string) (string, string) {
	raw := strings.ToLower(strings.TrimSpace(username))
	if raw == "" {
		return "", ""
	}
	parts := strings.SplitN(raw, "@", 2)
	if len(parts) != 2 {
		return "", ""
	}
	user := sanitizeKopiaUsernamePart(parts[0], "")
	host := sanitizeKopiaUsernamePart(parts[1], "")
	if user == "" || host == "" {
		return "", ""
	}
	return user, host
}

func commandResult(res process.Result) map[string]any {
	stdoutTotal := res.StdoutTotalBytes
	if stdoutTotal == 0 && res.Stdout != "" {
		stdoutTotal = int64(len(res.Stdout))
	}
	stderrTotal := res.StderrTotalBytes
	if stderrTotal == 0 && res.Stderr != "" {
		stderrTotal = int64(len(res.Stderr))
	}
	out := map[string]any{
		"exit_code":          res.ExitCode,
		"stdout":             res.Stdout,
		"stderr":             res.Stderr,
		"stdout_total_bytes": stdoutTotal,
		"stderr_total_bytes": stderrTotal,
		"stdout_truncated":   res.StdoutTruncated,
		"stderr_truncated":   res.StderrTruncated,
	}
	if res.Stdout != "" {
		out["stdout_tail"] = tailLines(res.Stdout, 20)
	}
	if res.Stderr != "" {
		out["stderr_tail"] = tailLines(res.Stderr, 20)
	}
	return out
}

func repositoryCommandFailureMessage(
	res process.Result,
	fallback error,
	spec repositorySpec,
	p Payload,
) string {
	message := ""
	for _, output := range []string{res.Stderr, res.Stdout} {
		if candidate := strings.TrimSpace(output); candidate != "" {
			message = candidate
			break
		}
	}
	if message == "" && fallback != nil {
		message = fallback.Error()
	}
	if message == "" {
		message = "repository command failed"
	}
	return redactManagedRestoreSecrets(tailLines(message, 20), spec, p)
}

func redactedRepositoryCommandResult(
	res process.Result,
	spec repositorySpec,
	p Payload,
) map[string]any {
	result := commandResult(res)
	redacted, ok := redactManagedRestoreValue(result, spec, p).(map[string]any)
	if ok {
		return redacted
	}
	return result
}

func repositoryCreateAlreadyExists(res process.Result) bool {
	output := strings.ToLower(res.Stdout + "\n" + res.Stderr)
	for _, token := range []string{
		"already exists",
		"already initialized",
		"repository exists",
		"found existing data in storage location",
	} {
		if strings.Contains(output, token) {
			return true
		}
	}
	return false
}

func parseSnapshotBrowseOutput(stdout string, basePath string, snapshotID string) []map[string]any {
	trimmed := strings.TrimSpace(stdout)
	if trimmed == "" {
		return nil
	}
	parsed, ok := decodeJSONLoose(trimmed)
	if !ok {
		return parseSnapshotBrowseTextOutput(trimmed, basePath)
	}
	rows := make([]map[string]any, 0)
	collectSnapshotEntries(parsed, &rows, strings.Trim(strings.TrimSpace(basePath), "/\\"), strings.TrimSpace(snapshotID))
	return rows
}

func snapshotBrowseJsonUnsupported(res process.Result) bool {
	output := strings.ToLower(res.Stdout + "\n" + res.Stderr)
	return strings.Contains(output, "unknown long flag '--json'") ||
		strings.Contains(output, "unknown flag: --json") ||
		strings.Contains(output, "flag provided but not defined: -json")
}

func snapshotBrowseFailureMessage(res process.Result, runErr error) string {
	stderr := strings.TrimSpace(res.Stderr)
	stdout := strings.TrimSpace(res.Stdout)
	switch {
	case stderr != "":
		return truncateSnapshotBrowseError("Snapshot browse failed: "+stderr, 2000)
	case stdout != "":
		return truncateSnapshotBrowseError("Snapshot browse failed: "+stdout, 2000)
	case runErr != nil:
		return runErr.Error()
	default:
		return "Snapshot browse failed"
	}
}

func truncateSnapshotBrowseError(value string, limit int) string {
	if limit <= 0 || len(value) <= limit {
		return value
	}
	return value[:limit]
}

// formatModTimeUTC parses a time string from Kopia output and returns it as
// RFC 3339 (ISO 8601) in UTC. Kopia formats ls -l timestamps in the Agent's
// local timezone, including ambiguous abbreviations such as CST, so named
// zones must be resolved against that same local timezone.
func formatModTimeUTC(raw string) string {
	return formatModTimeUTCInLocation(raw, time.Local)
}

func formatModTimeUTCInLocation(raw string, local *time.Location) string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return ""
	}
	if local == nil {
		local = time.Local
	}
	for _, layout := range []string{
		time.RFC3339Nano,
		"2006-01-02 15:04:05.999999999 -07:00",
		"2006-01-02 15:04:05.999999999 -0700",
	} {
		if parsed, err := time.Parse(layout, raw); err == nil {
			return parsed.UTC().Format(time.RFC3339)
		}
	}
	for _, layout := range []string{
		"Jan 2 2006 MST",
		"Jan 2 15:04 MST",
		"Jan 2 15:04:05 MST",
		"2006-01-02 15:04:05 MST",
		"2006-01-02T15:04:05 MST",
	} {
		parsed, err := time.ParseInLocation(layout, raw, local)
		if err != nil {
			continue
		}
		year := parsed.Year()
		if year == 0 {
			year = time.Now().In(local).Year()
		}
		inputZone := strings.Fields(raw)
		if len(inputZone) > 0 && (strings.EqualFold(inputZone[len(inputZone)-1], "UTC") ||
			strings.EqualFold(inputZone[len(inputZone)-1], "GMT")) {
			return time.Date(
				year,
				parsed.Month(),
				parsed.Day(),
				parsed.Hour(),
				parsed.Minute(),
				parsed.Second(),
				parsed.Nanosecond(),
				time.UTC,
			).Format(time.RFC3339)
		}
		localTime := time.Date(
			year,
			parsed.Month(),
			parsed.Day(),
			parsed.Hour(),
			parsed.Minute(),
			parsed.Second(),
			parsed.Nanosecond(),
			local,
		)
		localZone, _ := localTime.Zone()
		if len(inputZone) == 0 || !strings.EqualFold(inputZone[len(inputZone)-1], localZone) {
			continue
		}
		return localTime.UTC().Format(time.RFC3339)
	}
	for _, layout := range []string{
		"Jan 2 2006",
		"Jan 2 15:04",
		"Jan 2 15:04:05",
		"2006-01-02 15:04:05",
		"2006-01-02T15:04:05",
	} {
		parsed, err := time.ParseInLocation(layout, raw, local)
		if err != nil {
			continue
		}
		year := parsed.Year()
		if year == 0 {
			year = time.Now().In(local).Year()
		}
		return time.Date(
			year,
			parsed.Month(),
			parsed.Day(),
			parsed.Hour(),
			parsed.Minute(),
			parsed.Second(),
			parsed.Nanosecond(),
			local,
		).UTC().Format(time.RFC3339)
	}
	return raw
}

func parseSnapshotBrowseLongLine(line string) (mode string, size int64, modTime string, name string, ok bool) {
	fields := strings.Fields(line)
	if len(fields) < 7 || !looksLikeMode(fields[0]) {
		return "", 0, "", "", false
	}
	mode = fields[0]
	size, _ = strconv.ParseInt(fields[1], 10, 64)
	modTime = formatModTimeUTC(strings.Join(fields[2:5], " "))
	objectID := fields[5]
	idx := strings.Index(line, objectID)
	if idx < 0 {
		name = strings.Join(fields[6:], " ")
	} else {
		name = strings.TrimSpace(line[idx+len(objectID):])
	}
	name = strings.Trim(name, "/\\")
	if name == "" {
		return "", 0, "", "", false
	}
	return mode, size, modTime, name, true
}

func parseInsightSnapshotLongLine(line string) (mode string, size int64, modTime string, name string, ok bool) {
	fields := strings.Fields(line)
	if len(fields) < 7 || !looksLikeMode(fields[0]) {
		return "", 0, "", "", false
	}
	parsedSize, err := strconv.ParseInt(fields[1], 10, 64)
	if err != nil {
		return "", 0, "", "", false
	}
	mode, _, modTime, name, ok = parseSnapshotBrowseLongLine(line)
	if !ok {
		return "", 0, "", "", false
	}
	return mode, parsedSize, modTime, name, true
}

func parseSnapshotBrowseTextOutput(stdout string, basePath string) []map[string]any {
	base := strings.Trim(strings.TrimSpace(basePath), "/\\")
	rows := make([]map[string]any, 0)
	for _, line := range strings.Split(stdout, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		if mode, size, modTime, name, parsed := parseSnapshotBrowseLongLine(line); parsed {
			isDir := strings.HasPrefix(strings.ToLower(mode), "d")
			path := normalizeSnapshotBrowsePath(name, name, base, "")
			rows = append(rows, map[string]any{
				"name":         snapshotBrowseName(name, path),
				"path":         path,
				"type":         mapSnapshotBrowseType(isDir),
				"is_dir":       isDir,
				"size_bytes":   size,
				"modified_at":  modTime,
				"downloadable": true,
				"has_children": nil,
			})
			continue
		}
		name := strings.Trim(line, "/\\")
		if name == "" {
			continue
		}
		path := normalizeSnapshotBrowsePath(name, name, base, "")
		rows = append(rows, map[string]any{
			"name":         snapshotBrowseName(name, path),
			"path":         path,
			"type":         "file",
			"is_dir":       false,
			"size_bytes":   int64(0),
			"modified_at":  "",
			"downloadable": true,
			"has_children": nil,
		})
	}
	return rows
}

func looksLikeMode(value string) bool {
	if len(value) < 10 {
		return false
	}
	first := value[0]
	return first == 'd' || first == '-' || first == 'l' || first == 'L' ||
		first == 's' || first == 'S' || first == 'p' || first == 'P' ||
		first == 'b' || first == 'c' || first == 'C' || first == 'D' ||
		first == 'w'
}

func mapSnapshotBrowseType(isDir bool) string {
	if isDir {
		return "dir"
	}
	return "file"
}

func collectSnapshotEntries(raw any, rows *[]map[string]any, basePath string, snapshotID string) {
	switch value := raw.(type) {
	case []any:
		for _, item := range value {
			collectSnapshotEntries(item, rows, basePath, snapshotID)
		}
	case map[string]any:
		if entries, ok := value["entries"]; ok {
			collectSnapshotEntries(entries, rows, basePath, snapshotID)
			return
		}
		if children, ok := value["children"]; ok {
			collectSnapshotEntries(children, rows, basePath, snapshotID)
			return
		}
		name := strings.TrimSpace(stringValue(firstPresent(value, "name", "path", "entry")))
		if name == "" {
			return
		}
		isDir := boolValue(firstPresent(value, "is_dir", "isDir", "dir", "directory"), false)
		typ := strings.ToLower(strings.TrimSpace(stringValue(firstPresent(value, "type", "mode"))))
		mode := strings.ToLower(strings.TrimSpace(stringValue(firstPresent(value, "mode", "permissions"))))
		if typ == "dir" || typ == "directory" || typ == "d" || typ == "folder" || strings.HasPrefix(mode, "d") {
			isDir = true
		}
		if typ == "" {
			if isDir {
				typ = "dir"
			} else {
				typ = "file"
			}
		} else if isDir {
			typ = "dir"
		} else if typ == "f" || typ == "regular" {
			typ = "file"
		}
		size, _ := int64Value(firstPresent(value, "size", "size_bytes", "length"))
		modTime := formatModTimeUTC(strings.TrimSpace(stringValue(firstPresent(value, "mod_time", "modified_at", "mtime", "modTime"))))
		path := normalizeSnapshotBrowsePath(
			strings.TrimSpace(stringValue(firstPresent(value, "path", "name"))),
			name,
			basePath,
			snapshotID,
		)
		*rows = append(*rows, map[string]any{
			"name":         snapshotBrowseName(name, path),
			"path":         path,
			"type":         typ,
			"is_dir":       isDir,
			"size_bytes":   size,
			"modified_at":  modTime,
			"downloadable": true,
			"has_children": nil,
		})
	}
}

func normalizeSnapshotBrowsePath(rawPath string, rawName string, basePath string, snapshotID string) string {
	path := filepath.ToSlash(strings.Trim(strings.TrimSpace(rawPath), "/\\"))
	if path == "" {
		path = filepath.ToSlash(strings.Trim(strings.TrimSpace(rawName), "/\\"))
	}
	snapshotPrefix := strings.Trim(strings.TrimSpace(snapshotID), "/\\")
	if snapshotPrefix != "" {
		if path == snapshotPrefix {
			path = ""
		} else if strings.HasPrefix(path, snapshotPrefix+"/") {
			path = strings.TrimPrefix(path, snapshotPrefix+"/")
		}
	}
	if basePath != "" && path != "" && path != basePath && !strings.HasPrefix(path, basePath+"/") {
		path = strings.TrimRight(basePath, "/") + "/" + filepath.Base(path)
	}
	return strings.Trim(path, "/")
}

func snapshotBrowseName(rawName string, path string) string {
	name := strings.Trim(strings.TrimSpace(rawName), "/\\")
	if name == "" {
		name = path
	}
	if name == "" {
		return ""
	}
	return filepath.Base(filepath.ToSlash(name))
}

func firstPresent(m map[string]any, keys ...string) any {
	for _, key := range keys {
		if value, ok := m[key]; ok {
			return value
		}
	}
	return nil
}

type kopiaProgressReporter struct {
	mu                    sync.Mutex
	lastPercent           int
	lastSentAt            time.Time
	lastSubstantiveAt     time.Time
	lastSignature         string
	lastProcessingCounter int64
	lastUploadCounter     int64
	lastPhase             string
	lastSequence          int64
	lastHashedCount       int64
	lastUploadedCount     int64
	lastSnapshot          kopia.ProgressSnapshot
	hasSnapshot           bool
	hashSpeedTracker      kopia.SpeedTracker
	uploadSpeedTracker    kopia.SpeedTracker
}

func newKopiaProgressReporter() *kopiaProgressReporter {
	now := time.Now()
	return &kopiaProgressReporter{lastPercent: -1, lastSubstantiveAt: now}
}

func (r *kopiaProgressReporter) noteSubstantive(snapshot kopia.ProgressSnapshot) {
	sig := fmt.Sprintf(
		"%d|%d|%d|%d|%s",
		kopia.ProcessingCounter(snapshot),
		snapshot.UploadedBytes,
		snapshot.HashedCount,
		snapshot.UploadedCount,
		snapshot.Phase,
	)
	r.mu.Lock()
	defer r.mu.Unlock()
	if sig != r.lastSignature {
		r.lastSignature = sig
		r.lastSubstantiveAt = time.Now()
	}
}

func (r *kopiaProgressReporter) stallExceeded(seconds int) bool {
	if seconds <= 0 {
		return false
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	return time.Since(r.lastSubstantiveAt) >= time.Duration(seconds)*time.Second
}

func kopiaProgressStallSeconds() int {
	raw := strings.TrimSpace(os.Getenv("AGENT_KOPIA_PROGRESS_STALL_SECONDS"))
	if raw == "" {
		return 2700
	}
	value, err := strconv.Atoi(raw)
	if err != nil || value <= 0 {
		return 2700
	}
	return value
}

func monitorKopiaProgressStall(
	ctx context.Context,
	cancel context.CancelFunc,
	reporter *kopiaProgressReporter,
	stallSeconds int,
	done <-chan struct{},
) {
	if stallSeconds <= 0 {
		return
	}
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-done:
			return
		case <-ticker.C:
			if reporter.stallExceeded(stallSeconds) {
				cancel()
				return
			}
		}
	}
}

func (r *kopiaProgressReporter) stabilizeSnapshot(snapshot kopia.ProgressSnapshot) kopia.ProgressSnapshot {
	r.mu.Lock()
	defer r.mu.Unlock()
	if snapshot.HashedCount > r.lastHashedCount {
		r.lastHashedCount = snapshot.HashedCount
	} else if snapshot.HashedCount == 0 && r.lastHashedCount > 0 {
		snapshot.HashedCount = r.lastHashedCount
	}
	if snapshot.UploadedCount > r.lastUploadedCount {
		r.lastUploadedCount = snapshot.UploadedCount
	} else if snapshot.UploadedCount == 0 && snapshot.UploadedBytes > 0 && r.lastUploadedCount > 0 {
		snapshot.UploadedCount = r.lastUploadedCount
	}
	return snapshot
}

func (r *kopiaProgressReporter) maybeSend(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	snapshot kopia.ProgressSnapshot,
) {
	snapshot = r.stabilizeSnapshot(snapshot)
	if snapshot.SchemaVersion < 2 && snapshot.Percent <= 0 &&
		snapshot.HashingCount == 0 &&
		snapshot.HashedCount == 0 &&
		snapshot.UploadedCount == 0 &&
		snapshot.UploadedBytes == 0 &&
		snapshot.HashedBytes == 0 {
		return
	}
	if snapshot.SchemaVersion < 2 && snapshot.Percent <= 0 {
		snapshot.Percent = 1
		snapshot.PercentValue = 1
		snapshot.PercentKnown = true
	}
	processingCounter := kopia.ProcessingCounter(snapshot)
	uploadCounter := snapshot.UploadedBytes
	now := time.Now()
	sampleTime := now
	if parsed, err := time.Parse(time.RFC3339Nano, snapshot.SampledAt); err == nil {
		sampleTime = parsed
	}
	hashSpeedBps, hashSpeedSource := r.hashSpeedTracker.Observe(processingCounter, sampleTime)
	uploadSpeedBps, uploadSpeedSource := r.uploadSpeedTracker.Observe(uploadCounter, sampleTime)
	r.mu.Lock()
	shouldSend := r.lastPercent < 0 ||
		snapshot.Percent > r.lastPercent ||
		processingCounter > r.lastProcessingCounter ||
		uploadCounter > r.lastUploadCounter ||
		snapshot.Phase != r.lastPhase ||
		snapshot.Sequence > r.lastSequence ||
		now.Sub(r.lastSentAt) >= 3*time.Second
	r.lastSnapshot = snapshot
	r.hasSnapshot = true
	if shouldSend {
		r.lastPercent = snapshot.Percent
		r.lastProcessingCounter = processingCounter
		r.lastUploadCounter = uploadCounter
		r.lastPhase = snapshot.Phase
		r.lastSequence = snapshot.Sequence
		r.lastSentAt = now
	}
	r.mu.Unlock()
	if shouldSend || snapshot.HashedBytes > 0 || snapshot.UploadedBytes > 0 {
		r.noteSubstantive(snapshot)
	}
	if !shouldSend {
		return
	}
	_ = sendProgress(ctx, rep, taskID, kopia.ProgressPayloadWithDualSpeed(
		snapshot,
		hashSpeedBps,
		hashSpeedSource,
		uploadSpeedBps,
		uploadSpeedSource,
	))
}

func (r *kopiaProgressReporter) completionPayload(snapshotID string) map[string]any {
	r.mu.Lock()
	snapshot := r.lastSnapshot
	hasSnapshot := r.hasSnapshot
	r.mu.Unlock()

	var payload map[string]any
	if hasSnapshot {
		payload = kopia.ProgressPayload(snapshot)
	} else {
		payload = map[string]any{
			"progress_schema_version": 1,
			"phase":                   "kopia_transfer",
			"processed_bytes":         int64(0),
			"uploaded_bytes":          int64(0),
			"bytes_done":              int64(0),
			"bytes_total":             int64(0),
			"bytes_total_known":       false,
		}
	}
	payload["phase"] = "kopia_transfer"
	payload["kopia_phase"] = "snapshot_created"
	payload["kopia_percent"] = 100.0
	payload["percent"] = 100.0
	payload["processing_speed_bps"] = int64(0)
	payload["processing_speed_source"] = "completed"
	payload["upload_speed_bps"] = int64(0)
	payload["upload_speed_source"] = "completed"
	payload["speed_bps"] = int64(0)
	payload["speed_source"] = "completed"
	payload["kopia_eta_seconds"] = int64(0)
	payload["kopia_snapshot_id"] = snapshotID
	return payload
}

func (r *kopiaProgressReporter) noteSubstantivePayload(payload map[string]any) {
	sig := fmt.Sprintf(
		"%v|%v|%v|%v",
		payload["bytes_done"],
		payload["uploaded_bytes"],
		payload["hashed_bytes"],
		payload["kopia_percent"],
	)
	r.mu.Lock()
	defer r.mu.Unlock()
	if sig != r.lastSignature {
		r.lastSignature = sig
		r.lastSubstantiveAt = time.Now()
	}
}

func (r *kopiaProgressReporter) maybeSendRestore(
	ctx context.Context,
	rep ReporterSink,
	taskID string,
	payload map[string]any,
) {
	percent := intValue(payload["kopia_percent"])
	if percent <= 0 {
		percent = intValue(payload["percent"])
	}
	now := time.Now()
	r.mu.Lock()
	shouldSend := r.lastPercent < 0 ||
		percent > r.lastPercent ||
		now.Sub(r.lastSentAt) >= 3*time.Second
	if shouldSend {
		r.lastPercent = percent
		r.lastSentAt = now
	}
	r.mu.Unlock()
	r.noteSubstantivePayload(payload)
	if !shouldSend {
		return
	}
	_ = sendProgress(ctx, rep, taskID, payload)
}

func intValue(raw any) int {
	switch value := raw.(type) {
	case int:
		return value
	case int64:
		return int(value)
	case float64:
		return int(value)
	case string:
		parsed, err := strconv.Atoi(strings.TrimSpace(value))
		if err == nil {
			return parsed
		}
	}
	return 0
}

func parseSnapshotOutput(stdout string) map[string]any {
	trimmed := strings.TrimSpace(stdout)
	if trimmed == "" {
		return nil
	}
	parsed, ok := decodeJSONLoose(trimmed)
	if !ok {
		return nil
	}
	return snapshotResultFromParsed(parsed)
}

func snapshotResultFromParsed(parsed any) map[string]any {
	result := map[string]any{
		"snapshot": parsed,
	}
	if failureSummary := snapshotFailureSummary(parsed); failureSummary != nil {
		// Keep a compact diagnostic alongside the full snapshot. The wire
		// result is intentionally bounded and may discard the large snapshot
		// tree before the backend can build user-facing failure details.
		result["snapshot_failure_summary"] = failureSummary
	}
	if id := findStringKey(parsed, "id", "snapshot_id", "snapshotID", "kopia_snapshot_id"); id != "" {
		result["kopia_snapshot_id"] = id
	}
	stats := map[string]any{}
	if size, ok := findIntKey(
		parsed,
		"size",
		"size_bytes",
		"sizeBytes",
		"total_size",
		"totalSize",
		"total_size_bytes",
		"totalSizeBytes",
		"total_file_size",
		"totalFileSize",
	); ok {
		stats["size_bytes"] = size
		result["size_bytes"] = size
	}
	if files, ok := findIntKey(
		parsed,
		"files",
		"file_count",
		"fileCount",
		"total_file_count",
		"totalFileCount",
		"total_files",
		"totalFiles",
		"num_files",
		"numFiles",
	); ok {
		stats["file_count"] = files
		result["file_count"] = files
	}
	if dirs, ok := findIntKey(
		parsed,
		"dirs",
		"dir_count",
		"dirCount",
		"directory_count",
		"directoryCount",
		"total_dir_count",
		"totalDirCount",
		"total_directory_count",
		"totalDirectoryCount",
		"total_directories",
		"totalDirectories",
		"num_directories",
		"numDirectories",
	); ok {
		stats["dir_count"] = dirs
		result["dir_count"] = dirs
	}
	if len(stats) > 0 {
		result["stats"] = stats
	}
	return result
}

func snapshotFailureSummary(parsed any) map[string]any {
	parsedMap, ok := parsed.(map[string]any)
	if !ok {
		return nil
	}
	rootEntry, ok := parsedMap["rootEntry"].(map[string]any)
	if !ok {
		return nil
	}
	summary, ok := rootEntry["summ"].(map[string]any)
	if !ok {
		return nil
	}
	errors, ok := summary["errors"].([]any)
	if !ok || len(errors) == 0 {
		return nil
	}
	collector := newSnapshotFailureCollector()
	for _, raw := range errors {
		item, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		collector.add(stringValue(item["path"]), stringValue(item["error"]))
	}
	collector.reportedTotal = len(errors)
	return collector.summary()
}

type snapshotFailureCollector struct {
	reportedTotal  int
	observedCount  int
	causeCounts    map[string]int
	itemTypes      map[string]string
	itemTypeCounts map[string]int
	items          []any
}

func newSnapshotFailureCollector() *snapshotFailureCollector {
	return &snapshotFailureCollector{
		causeCounts:    map[string]int{},
		itemTypes:      map[string]string{},
		itemTypeCounts: map[string]int{},
		items:          make([]any, 0, snapshotFailureSampleLimit),
	}
}

func (c *snapshotFailureCollector) observe(line string) {
	if match := snapshotFatalCountPattern.FindStringSubmatch(line); len(match) == 2 {
		if count, err := strconv.Atoi(match[1]); err == nil && count > c.reportedTotal {
			c.reportedTotal = count
		}
	}
	path, failure, ok := parseSnapshotFailureLine(line)
	if ok {
		c.add(path, failure)
	}
}

func (c *snapshotFailureCollector) add(path string, failure string) {
	path = strings.TrimSpace(path)
	failure = strings.TrimSpace(failure)
	if path == "" && failure == "" {
		return
	}
	cause, itemType := snapshotFailureCause(failure)
	c.observedCount++
	c.causeCounts[cause]++
	c.itemTypes[cause] = itemType
	c.itemTypeCounts[itemType]++
	if len(c.items) >= snapshotFailureSampleLimit {
		return
	}
	c.items = append(c.items, map[string]any{
		"path":      truncateSnapshotFailureHead(path, snapshotFailurePathLimit),
		"error":     truncateFailureTail("", failure, snapshotFailureErrorLimit),
		"cause":     cause,
		"item_type": itemType,
	})
}

func (c *snapshotFailureCollector) summary() map[string]any {
	total := max(c.reportedTotal, c.observedCount)
	if total == 0 {
		return nil
	}
	causeCounts := make(map[string]int, len(c.causeCounts)+1)
	itemTypes := make(map[string]string, len(c.itemTypes)+1)
	itemTypeCounts := make(map[string]int, len(c.itemTypeCounts)+1)
	for cause, count := range c.causeCounts {
		causeCounts[cause] = count
		itemTypes[cause] = c.itemTypes[cause]
	}
	for itemType, count := range c.itemTypeCounts {
		itemTypeCounts[itemType] = count
	}
	if unclassified := total - c.observedCount; unclassified > 0 {
		causeCounts["snapshot_errors"] += unclassified
		itemTypes["snapshot_errors"] = "unknown"
		itemTypeCounts["unknown"] += unclassified
	}
	return map[string]any{
		"total_count":      total,
		"reported_count":   len(c.items),
		"truncated":        total > len(c.items),
		"cause_counts":     causeCounts,
		"item_types":       itemTypes,
		"item_type_counts": itemTypeCounts,
		"items":            c.items,
	}
}

func parseSnapshotFailureLine(line string) (string, string, bool) {
	const marker = `error when processing "`
	lower := strings.ToLower(line)
	start := strings.Index(lower, marker)
	if start < 0 {
		return "", "", false
	}
	rest := line[start+len(marker):]
	separator := strings.Index(rest, `":`)
	if separator < 0 {
		return "", "", false
	}
	return strings.TrimSpace(rest[:separator]), strings.TrimSpace(rest[separator+2:]), true
}

func snapshotFailureCause(failure string) (string, string) {
	lower := strings.ToLower(failure)
	if strings.Contains(lower, "unknown or unsupported entry type") ||
		strings.Contains(lower, "unsupported entry type") ||
		strings.Contains(lower, "unsupported filesystem entry") ||
		strings.Contains(lower, "unix socket") ||
		strings.Contains(lower, "socket") ||
		strings.Contains(lower, "named pipe") {
		return "unsupported_entry_type", "special"
	}
	itemType := "file"
	for _, marker := range []string{
		"readdir", "read directory", "open directory", "cannot list directory",
		"unable to list directory", "cannot create iterator", "unable to read directory",
	} {
		if strings.Contains(lower, marker) {
			itemType = "directory"
			break
		}
	}
	if strings.Contains(lower, "operation not permitted") {
		return "macos_privacy_denied", itemType
	}
	if strings.Contains(lower, "permission denied") || strings.Contains(lower, "access denied") {
		return "permission_denied", itemType
	}
	if itemType == "directory" {
		return "unreadable_directory", itemType
	}
	return "unreadable_file", itemType
}

func truncateSnapshotFailureHead(value string, limit int) string {
	value = strings.TrimSpace(value)
	if limit <= 0 || len(value) <= limit {
		return value
	}
	end := limit - len("...")
	for end > 0 && !utf8.RuneStart(value[end]) {
		end--
	}
	return value[:end] + "..."
}

func compactSnapshotFailureProjection(summary map[string]any) map[string]any {
	return map[string]any{
		"rootEntry": map[string]any{
			"summ": map[string]any{
				"errors": summary["items"],
			},
		},
	}
}

func decodeJSONLoose(raw string) (any, bool) {
	var parsed any
	if json.Unmarshal([]byte(raw), &parsed) == nil {
		return parsed, true
	}
	lines := strings.Split(raw, "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if line == "" {
			continue
		}
		if json.Unmarshal([]byte(line), &parsed) == nil {
			return parsed, true
		}
	}
	return nil, false
}

func findStringKey(raw any, keys ...string) string {
	switch value := raw.(type) {
	case map[string]any:
		for _, key := range keys {
			if v, ok := value[key]; ok {
				text := strings.TrimSpace(stringValue(v))
				if text != "" {
					return text
				}
			}
		}
		for _, nested := range value {
			if found := findStringKey(nested, keys...); found != "" {
				return found
			}
		}
	case []any:
		for _, nested := range value {
			if found := findStringKey(nested, keys...); found != "" {
				return found
			}
		}
	}
	return ""
}

func findIntKey(raw any, keys ...string) (int64, bool) {
	switch value := raw.(type) {
	case map[string]any:
		for _, key := range keys {
			if v, ok := value[key]; ok {
				if parsed, ok := int64Value(v); ok {
					return parsed, true
				}
			}
		}
		for _, nested := range value {
			if parsed, ok := findIntKey(nested, keys...); ok {
				return parsed, true
			}
		}
	case []any:
		for _, nested := range value {
			if parsed, ok := findIntKey(nested, keys...); ok {
				return parsed, true
			}
		}
	}
	return 0, false
}
