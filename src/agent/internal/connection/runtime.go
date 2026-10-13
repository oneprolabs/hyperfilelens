package connection

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/shirou/gopsutil/v4/process"
	"hyperfilelens/agent/internal/infra/config"
)

func agentRunning(root string) (bool, error) {
	ps, e := process.Processes()
	if e != nil {
		return false, e
	}
	exe := filepath.Join(root, "bin", "hfl-agent")
	if runtime.GOOS == "windows" {
		exe += ".exe"
	}
	if resolved, e := filepath.EvalSymlinks(exe); e == nil {
		exe = resolved
	}
	for _, p := range ps {
		if p.Pid == int32(os.Getpid()) {
			continue
		}
		path, e := p.Exe()
		if e != nil {
			continue
		}
		if strings.EqualFold(path, exe) {
			args, e := p.CmdlineSlice()
			if e != nil {
				return false, e
			}
			if len(args) < 2 || args[1] == "run" {
				for _, arg := range args[min(2, len(args)):] {
					if strings.Contains(arg, "wss") || strings.Contains(arg, "api-base") {
						return false, errors.New("service command-line address overrides must be removed before configure")
					}
				}
				return true, nil
			}
		}
	}
	return false, nil
}
func agentService(ctx context.Context, root, action string) error {
	var cmd *exec.Cmd
	if runtime.GOOS == "windows" {
		cmd = exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", filepath.Join(root, "bin", "install.ps1"), action, "-QuietFooter")
	} else {
		cmd = exec.CommandContext(ctx, filepath.Join(root, "bin", "install.sh"), action)
	}
	installed, parseErr := config.ParseEnvFile(filepath.Join(root, "config", "agent.env"))
	if parseErr != nil {
		return parseErr
	}
	cmd.Env = append(os.Environ(), "HFL_INSTALLATION_MODE="+installed["HFL_INSTALLATION_MODE"], "HFL_RUN_AS_USER="+installed["HFL_RUN_AS_USER"], "HFL_RUN_AS_HOME="+installed["HFL_RUN_AS_HOME"], "HFL_CONFIGURE_OWNER_PID="+strconv.Itoa(os.Getpid()), "HFL_AGENT_ROOT="+root, "HFL_DATA_DIR="+root)
	if _, e := cmd.CombinedOutput(); e != nil {
		return fmt.Errorf("Agent %s failed; inspect the local lifecycle log", action)
	}
	return nil
}

type composeRuntime struct{ command []string }

// Select once per maintenance/recovery operation, not by retrying a failed
// mutating command with a different Compose implementation.
func (c *composeRuntime) resolve(ctx context.Context) error {
	if len(c.command) > 0 {
		return nil
	}
	versionRE := regexp.MustCompile(`[vV]?(\d+)\.(\d+)(?:\.(\d+))?`)
	for _, candidate := range [][]string{{"docker", "compose"}, {"docker-compose"}} {
		path, err := exec.LookPath(candidate[0])
		if err != nil {
			continue
		}
		for _, suffix := range [][]string{{"version", "--short"}, {"version"}} {
			args := append(append([]string{}, candidate[1:]...), suffix...)
			out, err := exec.CommandContext(ctx, path, args...).Output()
			if err != nil {
				continue
			}
			match := versionRE.FindStringSubmatch(string(out))
			if len(match) == 0 {
				continue
			}
			major, _ := strconv.Atoi(match[1])
			minor, _ := strconv.Atoi(match[2])
			// Match existing Gateway requirements: Compose v2 >= 2.20.0.
			if major > 2 || major == 2 && minor >= 20 {
				c.command = append([]string{path}, candidate[1:]...)
				return nil
			}
		}
	}
	return errors.New("managed AI engine requires Docker Compose v2 >= 2.20.0 (docker compose or docker-compose)")
}

func (c *composeRuntime) run(ctx context.Context, root string, args ...string) ([]byte, error) {
	if err := c.resolve(ctx); err != nil {
		return nil, err
	}
	dir := filepath.Join(root, "runtime", "lensnode")
	base := append(append([]string{}, c.command[1:]...), "-p", "hyperfilelens-gateway", "-f", filepath.Join(dir, "docker-compose.yml"))
	cmd := exec.CommandContext(ctx, c.command[0], append(base, args...)...)
	cmd.Dir = dir
	out, e := cmd.Output()
	if e != nil {
		return nil, errors.New("managed AI engine Compose operation failed; inspect Docker status")
	}
	return out, nil
}
func (c *composeRuntime) lensRunning(ctx context.Context, root string) (bool, error) {
	out, e := c.run(ctx, root, "ps", "-a", "-q", "lensnode")
	if e != nil {
		return false, e
	}
	id := strings.TrimSpace(string(out))
	if id == "" || strings.Contains(id, "\n") {
		return false, errors.New("existing LensNode container is missing or ambiguous")
	}
	cmd := exec.CommandContext(ctx, "docker", "inspect", id)
	out, e = cmd.Output()
	var containers []struct {
		State  struct{ Running bool }
		Config struct {
			Labels map[string]string
			Env    []string
		}
	}
	if e != nil || json.Unmarshal(out, &containers) != nil || len(containers) != 1 {
		return false, errors.New("cannot verify LensNode container state")
	}
	container := containers[0]
	labels := container.Config.Labels
	if labels["com.hyperfilelens.managed"] != "true" || labels["com.hyperfilelens.component"] != "gateway-lensnode" || filepath.Clean(labels["com.docker.compose.project.working_dir"]) != filepath.Join(root, "runtime", "lensnode") {
		return false, errors.New("AI engine container is not owned by this installed gateway")
	}
	env, e := config.ParseEnvFile(filepath.Join(root, "config", "lensnode.env"))
	if e != nil {
		return false, e
	}
	matches := false
	for _, value := range container.Config.Env {
		if value == "LENSNODE_TOKEN="+env["LENSNODE_TOKEN"] {
			matches = true
		}
	}
	if !matches {
		return false, errors.New("AI engine container credentials do not match this installed gateway")
	}
	return container.State.Running, nil
}
func (c *composeRuntime) stopLens(ctx context.Context, root string) error {
	_, e := c.run(ctx, root, "stop", "lensnode")
	return e
}
func (c *composeRuntime) applyLens(ctx context.Context, root string, running bool) error {
	if _, e := c.run(ctx, root, "config", "--quiet"); e != nil {
		return e
	}
	if !running {
		_, e := c.run(ctx, root, "up", "--no-start", "--no-deps", "--force-recreate", "--pull", "never", "--no-build", "lensnode")
		return e
	}
	_, e := c.run(ctx, root, "up", "-d", "--no-deps", "--force-recreate", "--pull", "never", "--no-build", "lensnode")
	return e
}

func installedRuntime() runtimeHooks {
	c := &composeRuntime{}
	return runtimeHooks{
		agentRunning: agentRunning, agentService: agentService,
		lensRunning: c.lensRunning, stopLens: c.stopLens, applyLens: c.applyLens,
		lockLens: lockSidecar,
	}
}

func waitStopped(ctx context.Context, check func() (bool, error)) error {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	for {
		running, err := check()
		if err != nil {
			return err
		}
		if !running {
			return nil
		}
		select {
		case <-ctx.Done():
			return errors.New("component stop was not confirmed; configuration must not be written")
		case <-time.After(100 * time.Millisecond):
		}
	}
}

func (ops runtimeHooks) stopAgent(ctx context.Context, root string) error {
	if err := ops.agentService(ctx, root, "stop"); err != nil {
		return err
	}
	return waitStopped(ctx, func() (bool, error) { return ops.agentRunning(root) })
}

func (ops runtimeHooks) stopAIEngine(ctx context.Context, root string) error {
	if err := ops.stopLens(ctx, root); err != nil {
		return err
	}
	return waitStopped(ctx, func() (bool, error) { return ops.lensRunning(ctx, root) })
}

// Before any file was changed, do not restore snapshots over a still-running
// process. Only resume components that were actually stopped.
func (ops runtimeHooks) resumeOriginal(ctx context.Context, root string, t transaction) error {
	if t.LensRunning {
		running, err := ops.lensRunning(ctx, root)
		if err != nil {
			return err
		}
		if !running {
			if err := ops.applyLens(ctx, root, true); err != nil {
				return err
			}
		}
	}
	if t.AgentRunning {
		running, err := ops.agentRunning(root)
		if err != nil {
			return err
		}
		if !running {
			return ops.agentService(ctx, root, "start")
		}
	}
	return nil
}
