package kopialayout

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type demandDrivenCapabilityEntry struct {
	gate   chan struct{}
	result *bool
}

type demandDrivenCapabilityCache struct {
	entries sync.Map
	probe   func(context.Context, string) (bool, error)
}

var binarySupport demandDrivenCapabilityCache

// ProbeDemandDriven caches only successful observations, including a successful
// help invocation without the flag. Execution failures are unknown, not evidence
// that a patched binary is unsupported.
func ProbeDemandDriven(ctx context.Context, bin string) (bool, error) {
	return binarySupport.load(ctx, bin)
}

// SupportsDemandDriven is for callers requiring positive capability evidence,
// not for deciding whether an already-applied cache policy can be discarded.
func SupportsDemandDriven(ctx context.Context, bin string) bool {
	supported, err := ProbeDemandDriven(ctx, bin)
	return err == nil && supported
}

func (c *demandDrivenCapabilityCache) load(ctx context.Context, bin string) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	absolute, err := filepath.Abs(bin)
	if err != nil {
		return false, fmt.Errorf("Kopia cache capability executable is unavailable")
	}
	info, err := os.Stat(absolute)
	if err != nil {
		return false, fmt.Errorf("Kopia cache capability executable is unavailable")
	}
	key := fmt.Sprintf("%s\x00%d\x00%d", absolute, info.Size(), info.ModTime().UnixNano())
	value, _ := c.entries.LoadOrStore(key, &demandDrivenCapabilityEntry{gate: make(chan struct{}, 1)})
	entry := value.(*demandDrivenCapabilityEntry)
	select {
	case entry.gate <- struct{}{}:
		defer func() { <-entry.gate }()
	case <-ctx.Done():
		return false, ctx.Err()
	}
	if err := ctx.Err(); err != nil {
		return false, err
	}
	if entry.result != nil {
		return *entry.result, nil
	}
	probe := c.probe
	if probe == nil {
		probe = probeDemandDriven
	}
	supported, err := probe(ctx, absolute)
	if err != nil {
		return false, err
	}
	if err := ctx.Err(); err != nil {
		return false, err
	}
	entry.result = &supported
	return supported, nil
}

func probeDemandDriven(ctx context.Context, bin string) (bool, error) {
	probe, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	cmd := exec.CommandContext(probe, bin, "--disable-file-logging", "--disable-content-log", "cache", "set", "--help")
	var output limitedOutput
	cmd.Stdout, cmd.Stderr = &output, &output
	if err := cmd.Run(); err != nil {
		if probe.Err() != nil {
			return false, fmt.Errorf("Kopia cache capability check interrupted: %w", probe.Err())
		}
		// CLI initialization can emit credentials or configuration. Do not
		// expose stdout/stderr or retain this failure as a capability result.
		return false, fmt.Errorf("Kopia cache capability check failed; retry the operation")
	}
	if err := probe.Err(); err != nil {
		return false, err
	}
	return strings.Contains(output.String(), "cache-demand-driven"), nil
}

type limitedOutput struct{ data []byte }

func (b *limitedOutput) Write(p []byte) (int, error) {
	n := len(p)
	if remaining := 65536 - len(b.data); remaining > 0 {
		if len(p) > remaining {
			p = p[:remaining]
		}
		b.data = append(b.data, p...)
	}
	return n, nil
}
func (b *limitedOutput) String() string { return string(b.data) }
