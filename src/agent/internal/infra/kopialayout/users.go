package kopialayout

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	gprocess "github.com/shirou/gopsutil/v4/process"
)

// CheckUsers is also used by the incoming installer's pre-stop preflight. Old
// Agents do not know our migration lock, so an occupied installation is never
// migrated solely because that lock was acquired successfully.
func CheckUsers(ctx context.Context, root string, allowAgent bool) error {
	root, err := physicalPath(root)
	if err != nil {
		return err
	}
	processes, err := gprocess.ProcessesWithContext(ctx)
	if err != nil {
		return fmt.Errorf("cannot verify cache users: %w", err)
	}
	for _, p := range processes {
		if p.Pid == int32(os.Getpid()) {
			continue
		}
		name, err := p.NameWithContext(ctx)
		if err != nil {
			continue // An unrelated process may have exited during enumeration.
		}
		name = strings.TrimSuffix(strings.ToLower(name), ".exe")
		if name != "kopia" && name != "hfl-agent" {
			continue
		}
		if name == "hfl-agent" && allowAgent {
			continue
		}
		exe, exeErr := p.ExeWithContext(ctx)
		args, argsErr := p.CmdlineSliceWithContext(ctx)
		if name == "hfl-agent" && len(args) > 1 {
			switch args[1] {
			case "version", "help", "--help", "-h", "package", "cache-layout":
				continue // Read-only verifiers do not use repository state.
			}
		}
		if exeErr != nil && argsErr != nil {
			if exists, _ := p.IsRunningWithContext(ctx); !exists {
				continue
			}
			return fmt.Errorf("cannot inspect a possible cache user; migration deferred")
		}
		owned := within(root, exe)
		for _, arg := range args {
			value := arg
			if _, after, ok := strings.Cut(arg, "="); ok {
				value = after
			}
			if filepath.IsAbs(value) && (samePath(root, value) || within(root, value)) {
				owned = true
			}
			if filepath.IsAbs(value) {
				if resolved, err := physicalPath(value); err == nil &&
					(samePath(root, resolved) || within(root, resolved)) {
					owned = true
				}
			}
		}
		if owned {
			return fmt.Errorf("CACHE_LAYOUT_BUSY: installation still has an active %s process; wait for it to exit", name)
		}
		// An externally launched Agent or PATH-installed Kopia may identify its
		// installation only through environment variables. In particular,
		// HFL_AGENT_HOME does not have to appear in the process command line.
		// Never print that secret-bearing environment.
		if name == "kopia" || name == "hfl-agent" {
			env, err := processEnvironment(ctx, p)
			if err != nil {
				if exists, _ := p.IsRunningWithContext(ctx); !exists {
					continue
				}
				return fmt.Errorf("cannot verify repository user's environment; migration deferred")
			}
			for _, pair := range env {
				key, value, _ := strings.Cut(pair, "=")
				switch key {
				case "KOPIA_CONFIG_PATH", "KOPIA_CACHE_DIRECTORY", "HFL_DATA_DIR", "HFL_AGENT_ROOT", "HFL_AGENT_HOME", "HOME", "USERPROFILE":
					resolved, err := physicalPath(value)
					if err == nil && (samePath(root, resolved) || within(root, resolved)) {
						return fmt.Errorf("CACHE_LAYOUT_BUSY: installation still has an active %s process", name)
					}
				}
			}
		}
	}
	return ctx.Err()
}
