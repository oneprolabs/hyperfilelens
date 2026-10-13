package connection

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"

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
func compose(ctx context.Context, root string, args ...string) ([]byte, error) {
	dir := filepath.Join(root, "runtime", "lensnode")
	base := []string{"compose", "-p", "hyperfilelens-gateway", "-f", filepath.Join(dir, "docker-compose.yml")}
	cmd := exec.CommandContext(ctx, "docker", append(base, args...)...)
	cmd.Dir = dir
	out, e := cmd.Output()
	if e != nil {
		return nil, errors.New("managed AI engine Compose operation failed; inspect Docker status")
	}
	return out, nil
}
func lensRunning(ctx context.Context, root string) (bool, error) {
	out, e := compose(ctx, root, "ps", "-a", "-q", "lensnode")
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
func stopLens(ctx context.Context, root string) error {
	_, e := compose(ctx, root, "stop", "lensnode")
	return e
}
func applyLens(ctx context.Context, root string, running bool) error {
	if _, e := compose(ctx, root, "config", "--quiet"); e != nil {
		return e
	}
	if !running {
		_, e := compose(ctx, root, "up", "--no-start", "--no-deps", "--force-recreate", "--pull", "never", "--no-build", "lensnode")
		return e
	}
	_, e := compose(ctx, root, "up", "-d", "--no-deps", "--force-recreate", "--pull", "never", "--no-build", "lensnode")
	return e
}
