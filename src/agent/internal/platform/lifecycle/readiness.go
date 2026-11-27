package lifecycle

import (
	"context"
	"fmt"
	"os"
	"path/filepath"

	"github.com/shirou/gopsutil/v4/process"
)

const readyFile = "runtime/lifecycle/ready.json"

type Ready struct {
	Version      int    `json:"schema_version"`
	Root         string `json:"root"`
	PID          int32  `json:"pid"`
	ProcessStart int64  `json:"process_start"`
	Binary       string `json:"binary"`
	AgentVersion string `json:"agent_version"`
	Commit       string `json:"commit"`
}

func identity(ctx context.Context, pid int32) (int64, string, error) {
	p, err := process.NewProcessWithContext(ctx, pid)
	if err != nil {
		return 0, "", err
	}
	start, err := p.CreateTimeWithContext(ctx)
	if err != nil {
		return 0, "", err
	}
	exe, err := p.ExeWithContext(ctx)
	if err != nil {
		return 0, "", err
	}
	return start, filepath.Clean(exe), nil
}

// Publish is called only after local startup and recovery have completed.
func Publish(ctx context.Context, root, version, commit string) error {
	root, err := ResolveAgentRoot(root)
	if err != nil {
		return err
	}
	pid := int32(os.Getpid())
	start, binary, err := identity(ctx, pid)
	if err != nil {
		return err
	}
	return Write(root, readyFile, Ready{1, root, pid, start, binary, version, commit})
}

// Check never opens SQLite or starts migration. A reused PID cannot validate a
// previous startup's record. The installer also verifies the service executable.
func Check(ctx context.Context, root string, pid int32, version, commit string) error {
	root, err := ResolveAgentRoot(root)
	if err != nil {
		return err
	}
	var r Ready
	if err := Read(root, readyFile, &r); err != nil {
		return err
	}
	if r.Version != 1 || r.Root != root || r.PID != pid || r.AgentVersion != version || r.Commit != commit {
		return fmt.Errorf("Agent readiness identity does not match the requested service")
	}
	start, binary, err := identity(ctx, pid)
	if err != nil {
		return err
	}
	if start != r.ProcessStart || binary != r.Binary {
		return fmt.Errorf("Agent readiness belongs to a different process instance")
	}
	return nil
}
