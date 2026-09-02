//go:build !windows

package enroll

import (
	"bytes"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"

	"hyperfilelens/agent/internal/platform/install"
	"hyperfilelens/agent/internal/platform/tlsclient"
	"hyperfilelens/agent/internal/platform/vfs"
)

const (
	legacyLensEnvFilePath      = "/etc/hyperfilelens/lensnode.env"
	lensSidecarLockPath        = "/run/lock/hyperfilelens-gateway-sidecar.lock"
	lensSidecarLockHeldEnv     = "HFL_GATEWAY_SIDECAR_LOCK_HELD"
	lensSidecarScript          = "gateway-install-lensnode-sidecar.sh"
	lensnodeImageArchive       = "lensnode-image-linux-amd64.tar.gz"
	gatewayDockerInstallScript = "gateway-install-docker-ubuntu-amd64.sh"
	defaultLensnodeImage       = "hyperfilelens-sourcelens-lensnode:latest"
	gatewayMinDockerEngine     = "24.0.0"
	gatewayMinDockerCompose    = "2.20.0"
)

func gatewayAgentRoot(agentRoot string) string {
	root := strings.TrimSpace(agentRoot)
	if root == "" {
		root = "/opt/hyperfilelens-agent"
	}
	return filepath.Clean(root)
}

func gatewayLensPaths(agentRoot string) (string, string, string) {
	root := gatewayAgentRoot(agentRoot)
	runtimeDir := vfs.AgentLensnodeRuntimeDir(root)
	return filepath.Join(vfs.AgentConfigDir(root), "lensnode.env"),
		filepath.Join(runtimeDir, ".hfl-applied-config.sha256"),
		runtimeDir
}

func gatewayLegacyLensEnvPath(agentRoot string) string {
	if gatewayAgentRoot(agentRoot) == "/opt/hyperfilelens-agent" {
		return legacyLensEnvFilePath
	}
	return ""
}

var (
	sourceLensHealthOK     = regexp.MustCompile(`"health"\s*:\s*"OK"`)
	dockerComposeVersionRE = regexp.MustCompile(`[vV]?[0-9]+\.[0-9]+(?:\.[0-9]+)?`)
)

func checkSourceLensHealthViaConsole(ctx context.Context, cfg Config) error {
	base := strings.TrimRight(strings.TrimSpace(cfg.APIBase), "/")
	if base == "" {
		return fmt.Errorf("SourceLens health check requires console API base URL")
	}
	url := base + "/sourcelens/health"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return fmt.Errorf("SourceLens health request: %w", err)
	}
	client := &http.Client{Timeout: 20 * time.Second}
	if tlsclient.InsecureTLSEnabled() {
		client.Transport = tlsclient.Transport()
	}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("SourceLens unreachable at %s: %w", url, err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if resp.StatusCode < 200 || resp.StatusCode >= 400 {
		return fmt.Errorf("SourceLens health returned HTTP %s", resp.Status)
	}
	if !sourceLensHealthOK.Match(body) {
		return fmt.Errorf("SourceLens unhealthy at %s", url)
	}
	return nil
}

type lensSidecarRuntime struct {
	envPath        string
	appliedPath    string
	legacyEnvPath  string
	lockPath       string
	healthy        func() bool
	ensureImage    func(context.Context, Config) error
	installSidecar func(context.Context, Config, bool) error
}

func checkGatewayRuntimePreflight(ctx context.Context, cfg Config) gatewayRuntimePreflightResult {
	if err := checkSourceLensHealthViaConsole(ctx, cfg); err != nil {
		return gatewayRuntimePreflightResult{Err: err}
	}

	dockerReady := dockerRuntimeReady()
	if !dockerReady {
		if _, err := exec.LookPath("docker"); err == nil {
			return gatewayRuntimePreflightResult{Err: fmt.Errorf(
				"Docker is installed but does not meet the requirements (engine >= %s, Compose v2 >= %s, reachable daemon); HyperFileLens will not repair or replace it",
				gatewayMinDockerEngine,
				gatewayMinDockerCompose,
			)}
		}
	}

	names := []string{lensSidecarScript, lensnodeImageArchive}
	if !dockerReady {
		names = append(names, gatewayDockerInstallScript)
		archive := gatewayDockerArchiveName()
		if archive == "" {
			return gatewayRuntimePreflightResult{Err: fmt.Errorf(
				"no offline Docker bundle is available for this Ubuntu release",
			)}
		}
		names = append(names, archive)
	}

	base := strings.TrimRight(strings.TrimSpace(cfg.APIBase), "/") + "/media/gateway-bootstrap/"
	var downloadBytes uint64
	for _, name := range names {
		req, err := http.NewRequestWithContext(ctx, http.MethodHead, base+name, nil)
		if err != nil {
			return gatewayRuntimePreflightResult{Err: err}
		}
		client := &http.Client{Timeout: 20 * time.Second}
		if tlsclient.InsecureTLSEnabled() {
			client.Transport = tlsclient.Transport()
		}
		resp, err := client.Do(req)
		if err != nil {
			return gatewayRuntimePreflightResult{Err: fmt.Errorf("%s is unavailable: %w", name, err)}
		}
		_ = resp.Body.Close()
		if resp.StatusCode < 200 || resp.StatusCode >= 400 {
			return gatewayRuntimePreflightResult{Err: fmt.Errorf("%s returned HTTP %s", name, resp.Status)}
		}
		if resp.ContentLength > 0 {
			downloadBytes += uint64(resp.ContentLength)
		}
	}

	if dockerReady {
		warnings := []string{}
		if state := existingLensSidecarState(); state != "" && state != "running (healthy)" && state != "running" {
			warnings = append(warnings, "existing AI engine container is "+state+"; repair or reinstall will be attempted")
		}
		return gatewayRuntimePreflightResult{
			ExistingDocker: true,
			Detail: fmt.Sprintf(
				"Docker engine %s and Compose %s will be reused; AI engine artifacts are available",
				dockerEngineVersion(),
				dockerComposeVersion(),
			),
			RequiredSpace: downloadBytes * 3,
			Warnings:      warnings,
		}
	}
	return gatewayRuntimePreflightResult{
		Detail:        "Docker is not installed; verified offline Docker and AI engine bundles will be used",
		RequiredSpace: downloadBytes * 3,
	}
}

func existingLensSidecarState() string {
	for _, name := range []string{
		"hyperfilelens-gateway-lensnode-1",
		"hyperfilelens-gateway_lensnode_1",
	} {
		out, err := exec.Command(
			"docker", "inspect", "--format",
			"{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}",
			name,
		).Output()
		if err != nil {
			continue
		}
		fields := strings.Fields(string(out))
		if len(fields) == 0 {
			return "unknown"
		}
		if len(fields) > 1 {
			return fields[0] + " (" + fields[1] + ")"
		}
		return fields[0]
	}
	return ""
}

func gatewayDockerArchiveName() string {
	raw, err := os.ReadFile("/etc/os-release")
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(raw), "\n") {
		if !strings.HasPrefix(line, "VERSION_ID=") {
			continue
		}
		switch strings.Trim(strings.TrimPrefix(line, "VERSION_ID="), `"`) {
		case "20.04":
			return "docker-debs-ubuntu2004-amd64.tar.gz"
		case "22.04":
			return "docker-debs-ubuntu2204-amd64.tar.gz"
		case "24.04":
			return "docker-debs-ubuntu2404-amd64.tar.gz"
		}
	}
	return ""
}

func defaultLensSidecarRuntime() lensSidecarRuntime {
	agentRoot := os.Getenv("HFL_AGENT_ROOT")
	envPath, appliedPath, _ := gatewayLensPaths(agentRoot)
	return lensSidecarRuntime{
		envPath:        envPath,
		appliedPath:    appliedPath,
		legacyEnvPath:  gatewayLegacyLensEnvPath(agentRoot),
		lockPath:       lensSidecarLockPath,
		healthy:        lensSidecarHealthy,
		ensureImage:    ensureLensnodeImage,
		installSidecar: runLensSidecarInstaller,
	}
}

// InstallLensSidecar writes LensNode credentials and runs the bundled sidecar install script.
func InstallLensSidecar(ctx context.Context, cfg Config, lens LensSidecarConfig) error {
	runtime := defaultLensSidecarRuntime()
	runtime.envPath, runtime.appliedPath, _ = gatewayLensPaths(cfg.AgentRoot)
	runtime.legacyEnvPath = gatewayLegacyLensEnvPath(cfg.AgentRoot)
	return runtime.install(ctx, cfg, lens)
}

func (runtime lensSidecarRuntime) install(
	ctx context.Context,
	cfg Config,
	lens LensSidecarConfig,
) error {
	return withFileLock(ctx, runtime.lockPath, func() error {
		legacyLayoutPresent := legacyLensLayoutPresentAt(runtime.legacyEnvPath)
		legacyLayoutAdopted := legacyLensLayoutPendingAt(runtime.envPath, runtime.legacyEnvPath)
		if legacyLayoutAdopted {
			if err := markLegacyLensLayoutAdopted(runtime.appliedPath); err != nil {
				return err
			}
		}
		_, fingerprint, err := writeLensEnvFileAt(runtime.envPath, lens)
		if err != nil {
			return err
		}

		if runtime.healthy() &&
			!legacyLayoutPresent &&
			os.Getenv("HFL_FORCE_SIDECAR_INSTALL") != "1" &&
			lensConfigurationApplied(runtime.appliedPath, fingerprint) {
			logStep("AI engine is already running.")
			return nil
		}

		if err := runtime.ensureImage(ctx, cfg); err != nil {
			return err
		}
		if err := runtime.installSidecar(ctx, cfg, legacyLayoutAdopted); err != nil {
			return err
		}
		return markLensConfigurationApplied(runtime.appliedPath, fingerprint)
	})
}

// legacyLensLayoutPending is intentionally narrow: only an Agent-managed
// install may use it to tell the downloaded installer that the newly written
// control-plane configuration supersedes the pre-unified file. Direct script
// invocations retain strict conflict checks.
func legacyLensLayoutPendingAt(envPath, legacyPath string) bool {
	if legacyPath == "" || envPath == legacyPath {
		return false
	}
	if !legacyLensLayoutPresentAt(legacyPath) {
		return false
	}
	// Only authorize the migration marker before the canonical env file has
	// been created. If both files already exist, the downloaded sidecar script
	// must compare them and reject conflicting credentials instead of having a
	// pre-existing file silently marked as adopted.
	_, err := os.Stat(envPath)
	return os.IsNotExist(err)
}

func legacyLensLayoutPresentAt(legacyPath string) bool {
	if legacyPath == "" {
		return false
	}
	if _, err := os.Stat(legacyPath); err == nil {
		return true
	}
	// The old layout kept the Compose project beside lensnode.env. Either
	// artifact can survive a partially completed cleanup and must trigger one
	// more convergence attempt so the sidecar script can remove the remainder.
	legacyComposeDir := filepath.Join(filepath.Dir(legacyPath), "lensnode")
	_, err := os.Stat(legacyComposeDir)
	return err == nil
}

func markLegacyLensLayoutAdopted(appliedPath string) error {
	marker := filepath.Join(filepath.Dir(appliedPath), ".hfl-legacy-layout-adopted")
	if err := os.MkdirAll(filepath.Dir(marker), 0o700); err != nil {
		return fmt.Errorf("create legacy LensNode migration state directory: %w", err)
	}
	if err := writePrivateEnvAtomically(marker, []byte("managed-by=hfl-enroll\n")); err != nil {
		return fmt.Errorf("record legacy LensNode migration state: %w", err)
	}
	return nil
}

func ensureGatewayDocker(ctx context.Context, cfg Config) error {
	if dockerRuntimeReady() {
		logOK(fmt.Sprintf("Using existing Docker (engine %s).", dockerEngineVersion()))
		return nil
	}
	if _, err := exec.LookPath("docker"); err == nil {
		return fmt.Errorf("docker is installed but does not meet the requirements (engine >= %s, Compose v2 >= %s, reachable daemon); HFL will not repair or replace it", gatewayMinDockerEngine, gatewayMinDockerCompose)
	}
	scriptPath, cleanup, err := downloadGatewayBootstrapScript(ctx, cfg, gatewayDockerInstallScript)
	if err != nil {
		return err
	}
	defer cleanup()

	cmd := exec.CommandContext(ctx, "/bin/bash", scriptPath)
	cmd.Env = append(os.Environ(),
		"HFL_API_BASE="+strings.TrimRight(strings.TrimSpace(cfg.APIBase), "/"),
		"HFL_GATEWAY_BOOTSTRAP_BASE="+strings.TrimRight(strings.TrimSpace(cfg.APIBase), "/")+"/media/gateway-bootstrap",
		"HFL_INSECURE_TLS="+insecureTLSEnv(),
		"HFL_DOCKER_MIN_ENGINE="+gatewayMinDockerEngine,
		"HFL_COMPOSE_MIN_VERSION="+gatewayMinDockerCompose,
	)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("docker install script: %w", err)
	}
	if !dockerRuntimeReady() {
		return fmt.Errorf("docker is not ready after install")
	}
	return nil
}

func dockerRuntimeReady() bool {
	if _, err := exec.LookPath("docker"); err != nil {
		return false
	}
	if err := exec.Command("docker", "info").Run(); err != nil {
		return false
	}
	engine := strings.TrimSpace(dockerEngineVersion())
	if engine == "" || !dockerVersionGE(engine, gatewayMinDockerEngine) {
		return false
	}
	compose := strings.TrimSpace(dockerComposeVersion())
	if compose == "" || !dockerVersionGE(compose, gatewayMinDockerCompose) {
		return false
	}
	return true
}

func dockerComposeVersion() string {
	for _, candidate := range [][]string{{"docker", "compose"}, {"docker-compose"}} {
		version := dockerComposeCandidateVersion(candidate)
		if version != "" && dockerVersionGE(version, gatewayMinDockerCompose) {
			return version
		}
	}
	return ""
}

func dockerComposeCandidateVersion(candidate []string) string {
	for _, suffix := range [][]string{{"version", "--short"}, {"version"}} {
		args := append(append([]string{}, candidate[1:]...), suffix...)
		out, err := exec.Command(candidate[0], args...).Output()
		if err != nil {
			continue
		}
		version := dockerComposeVersionRE.FindString(string(out))
		if version != "" {
			return normalizeDockerVersion(version)
		}
	}
	return ""
}

func dockerEngineVersion() string {
	out, err := exec.Command("docker", "version", "--format", "{{.Server.Version}}").Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

func dockerVersionGE(have, want string) bool {
	have = normalizeDockerVersion(have)
	want = normalizeDockerVersion(want)
	cmd := exec.Command("dpkg", "--compare-versions", have, "ge", want)
	return cmd.Run() == nil
}

func normalizeDockerVersion(value string) string {
	value = strings.TrimSpace(value)
	return strings.TrimPrefix(strings.TrimPrefix(value, "v"), "V")
}

func lensSidecarHealthy() bool {
	if _, err := exec.LookPath("docker"); err != nil {
		return false
	}
	cmd := exec.Command("docker", "ps",
		"--filter", "name=hyperfilelens-gateway",
		"--filter", "status=running",
		"--format", "{{.Names}}",
	)
	out, err := cmd.Output()
	if err != nil {
		return false
	}
	for _, line := range strings.Split(string(out), "\n") {
		name := strings.TrimSpace(line)
		if name == "hyperfilelens-gateway-lensnode-1" || name == "hyperfilelens-gateway_lensnode_1" {
			return true
		}
	}
	return false
}

func writeLensEnvFileAt(path string, lens LensSidecarConfig) (bool, string, error) {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return false, "", fmt.Errorf("create %s: %w", dir, err)
	}

	lines := []string{
		"# HyperFileLens SourceLens LensNode sidecar (managed by hfl-enroll gateway-install)",
		"LENS_BASE_URL=" + quoteEnv(lens.LensBaseURL),
		"LENSNODE_TOKEN=" + quoteEnv(lens.LensnodeToken),
		"LENSNODE_UUID=" + quoteEnv(lens.LensnodeUUID),
		"HFL_WORKSPACE_ROOT=" + quoteEnv(lens.WorkspaceRoot),
	}
	if lens.LensnodeName != "" {
		lines = append(lines, "LENSNODE_NAME="+quoteEnv(lens.LensnodeName))
	}
	policyValues := lens.Observability.lensnodeEnvValues()
	for _, name := range []string{
		"SENTRY_ENABLED",
		"SENTRY_BACKEND_DSN",
		"SENTRY_ENVIRONMENT",
		"SENTRY_TRACES_SAMPLE_RATE",
		"HFL_SENTRY_LENSNODE_RELEASE",
	} {
		if value, present := policyValues[name]; present {
			lines = append(lines, name+"="+quoteEnv(value))
		}
	}
	content := strings.Join(lines, "\n") + "\n"
	fingerprint := fmt.Sprintf("%x", sha256.Sum256([]byte(content)))
	current, err := os.ReadFile(path)
	if err == nil && bytes.Equal(current, []byte(content)) {
		return false, fingerprint, nil
	}
	if err != nil && !os.IsNotExist(err) {
		return false, "", fmt.Errorf("read %s: %w", path, err)
	}
	if err := writePrivateEnvAtomically(path, []byte(content)); err != nil {
		return false, "", fmt.Errorf("write %s: %w", path, err)
	}
	return true, fingerprint, nil
}

// ConvergeGatewayLensObservability refreshes only LensNode Sentry settings.
// Missing/unhealthy LensNode workloads are left to the explicit Gateway lifecycle.
func ConvergeGatewayLensObservability(
	ctx context.Context,
	cfg Config,
	lens LensSidecarConfig,
) (bool, error) {
	runtime := defaultLensSidecarRuntime()
	runtime.envPath, runtime.appliedPath, _ = gatewayLensPaths(cfg.AgentRoot)
	runtime.legacyEnvPath = gatewayLegacyLensEnvPath(cfg.AgentRoot)
	return runtime.convergeObservability(ctx, cfg, lens)
}

func (runtime lensSidecarRuntime) convergeObservability(
	ctx context.Context,
	cfg Config,
	lens LensSidecarConfig,
) (bool, error) {
	changed := false
	err := withFileLock(ctx, runtime.lockPath, func() error {
		legacyLayoutPresent := legacyLensLayoutPresentAt(runtime.legacyEnvPath)
		legacyLayoutAdopted := legacyLensLayoutPendingAt(runtime.envPath, runtime.legacyEnvPath)
		if legacyLayoutAdopted {
			if err := markLegacyLensLayoutAdopted(runtime.appliedPath); err != nil {
				return err
			}
		}
		var fingerprint string
		var err error
		changed, fingerprint, err = writeLensEnvFileAt(runtime.envPath, lens)
		if err != nil {
			return err
		}
		if (lensConfigurationApplied(runtime.appliedPath, fingerprint) && !legacyLayoutPresent) ||
			!runtime.healthy() {
			return nil
		}
		if err := runtime.installSidecar(ctx, cfg, legacyLayoutAdopted); err != nil {
			return fmt.Errorf("refresh AI engine observability: %w", err)
		}
		if err := markLensConfigurationApplied(runtime.appliedPath, fingerprint); err != nil {
			return err
		}
		changed = true
		return nil
	})
	return changed, err
}

func lensConfigurationApplied(path, fingerprint string) bool {
	content, err := os.ReadFile(path)
	return err == nil && strings.TrimSpace(string(content)) == fingerprint
}

func markLensConfigurationApplied(path, fingerprint string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return fmt.Errorf("create AI engine state directory: %w", err)
	}
	if err := writePrivateEnvAtomically(path, []byte(fingerprint+"\n")); err != nil {
		return fmt.Errorf("record applied AI engine configuration: %w", err)
	}
	return nil
}

func withFileLock(ctx context.Context, path string, action func() error) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("create sidecar lock directory: %w", err)
	}
	lock, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return fmt.Errorf("open sidecar lock: %w", err)
	}
	defer lock.Close()

	for {
		err = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			break
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EAGAIN) {
			return fmt.Errorf("lock sidecar lifecycle: %w", err)
		}
		timer := time.NewTimer(250 * time.Millisecond)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
	defer func() { _ = syscall.Flock(int(lock.Fd()), syscall.LOCK_UN) }()
	return action()
}

func runLensSidecarInstaller(ctx context.Context, cfg Config, legacyLayoutAdopted bool) error {
	scriptPath, cleanup, err := downloadSidecarInstallScript(ctx, cfg)
	if err != nil {
		return err
	}
	defer cleanup()
	cmd := exec.CommandContext(ctx, "/bin/bash", scriptPath)
	envPath, _, composeDir := gatewayLensPaths(cfg.AgentRoot)
	cmd.Env = append(os.Environ(),
		"HFL_AGENT_ROOT="+gatewayAgentRoot(cfg.AgentRoot),
		"HFL_LENS_ENV_FILE="+envPath,
		"HFL_GATEWAY_COMPOSE_DIR="+composeDir,
		"HFL_INSECURE_TLS="+insecureTLSEnv(),
		"LENSNODE_IMAGE="+defaultLensnodeImage,
		lensSidecarLockHeldEnv+"=1",
	)
	if legacyLayoutAdopted {
		cmd.Env = append(cmd.Env, "HFL_LEGACY_LAYOUT_ADOPTED=1")
	}
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("sidecar install script: %w", err)
	}
	return nil
}

func quoteEnv(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return `""`
	}
	if !strings.ContainsAny(value, " \t$\"'\\") {
		return value
	}
	return `"` + strings.ReplaceAll(value, `"`, `\"`) + `"`
}

func ensureLensnodeImage(ctx context.Context, cfg Config) error {
	if dockerImageExists(defaultLensnodeImage) && lensnodeImageSupportsInsecureTLS(defaultLensnodeImage) {
		return nil
	}
	if dockerImageExists(defaultLensnodeImage) {
		logWarn("Local AI engine image lacks configurable TLS verification support; loading console bundle.")
	}

	workDir, err := os.MkdirTemp("", "hfl-lens-image-")
	if err != nil {
		return err
	}
	defer func() { _ = os.RemoveAll(workDir) }()

	url := strings.TrimRight(cfg.APIBase, "/") + "/media/gateway-bootstrap/" + lensnodeImageArchive
	archivePath := filepath.Join(workDir, lensnodeImageArchive)
	logStep("Downloading AI engine container image bundle.")
	if err := downloadResumableWithProgress(ctx, url, archivePath, "AI engine image bundle"); err != nil {
		return fmt.Errorf("download AI engine image bundle: %w", err)
	}

	logStep("Loading AI engine container image.")
	cmd := exec.CommandContext(ctx, "docker", "load", "-i", archivePath)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("Docker load AI engine image: %w", err)
	}
	for _, ref := range []string{
		defaultLensnodeImage,
		"sourcelens-lensnode:latest",
		"oneprocloud/sourcelens-lensnode:latest",
	} {
		if dockerImageExists(ref) {
			if !lensnodeImageSupportsInsecureTLS(ref) {
				return fmt.Errorf("AI engine image %s is missing configurable TLS verification support", ref)
			}
			return nil
		}
	}
	return fmt.Errorf("AI engine image not present after Docker load (expected %s)", defaultLensnodeImage)
}

func lensnodeImageSupportsInsecureTLS(ref string) bool {
	probe := `import ssl; import lensnode.tls as tls; from lensnode.config import load_config; config = load_config(); native = getattr(config, "tls_skip_verify", False) and hasattr(tls, "create_ssl_context") and tls.create_ssl_context(skip_verify=True).verify_mode == ssl.CERT_NONE; legacy = hasattr(tls, "tls_insecure_enabled") and tls.tls_insecure_enabled(); raise SystemExit(0 if native or legacy else 1)`
	cmd := exec.Command(
		"docker", "run", "--rm",
		"-e", "LENSNODE_TLS_SKIP_VERIFY=1",
		"-e", "LENSNODE_INSECURE_TLS=1",
		ref,
		"python", "-c", probe,
	)
	return cmd.Run() == nil
}

func dockerImageExists(ref string) bool {
	cmd := exec.Command("docker", "image", "inspect", ref)
	return cmd.Run() == nil
}

func downloadSidecarInstallScript(ctx context.Context, cfg Config) (scriptPath string, cleanup func(), err error) {
	return downloadGatewayBootstrapScript(ctx, cfg, lensSidecarScript)
}

func downloadGatewayBootstrapScript(ctx context.Context, cfg Config, name string) (scriptPath string, cleanup func(), err error) {
	workDir, err := os.MkdirTemp("", "hfl-gw-bootstrap-")
	if err != nil {
		return "", nil, err
	}
	cleanup = func() { _ = os.RemoveAll(workDir) }

	url := strings.TrimRight(cfg.APIBase, "/") + "/media/gateway-bootstrap/" + name
	dest := filepath.Join(workDir, name)
	if err := install.DownloadURL(ctx, url, dest); err != nil {
		cleanup()
		return "", nil, fmt.Errorf("download %s: %w", name, err)
	}
	if err := os.Chmod(dest, 0o755); err != nil {
		cleanup()
		return "", nil, err
	}
	return dest, cleanup, nil
}

func insecureTLSEnv() string {
	if os.Getenv("HFL_INSECURE_TLS") == "0" {
		return "0"
	}
	return "1"
}
