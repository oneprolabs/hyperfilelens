package engine

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type kopiaS3Capabilities struct {
	urlStyle        bool
	credentialsFile bool
}

type kopiaS3CapabilityEntry struct {
	gate   chan struct{}
	result *kopiaS3Capabilities
}

type kopiaS3CapabilityCache struct {
	entries sync.Map
	probe   func(context.Context, string) (kopiaS3Capabilities, error)
}

var managedKopiaS3Capabilities kopiaS3CapabilityCache

// load shares one successful help probe across concurrent tasks. Failed probes
// are not evidence of a missing feature and must remain retryable. A replaced
// binary gets a new entry instead of inheriting an old capability decision.
func (c *kopiaS3CapabilityCache) load(ctx context.Context, bin string) (kopiaS3Capabilities, error) {
	key := kopiaCapabilityCacheKey(bin)
	value, _ := c.entries.LoadOrStore(key, &kopiaS3CapabilityEntry{gate: make(chan struct{}, 1)})
	entry := value.(*kopiaS3CapabilityEntry)
	select {
	case entry.gate <- struct{}{}:
		defer func() { <-entry.gate }()
	case <-ctx.Done():
		return kopiaS3Capabilities{}, ctx.Err()
	}
	if err := ctx.Err(); err != nil {
		return kopiaS3Capabilities{}, err
	}
	if entry.result != nil {
		return *entry.result, nil
	}
	probe := c.probe
	if probe == nil {
		probe = probeKopiaS3Capabilities
	}
	result, err := probe(ctx, bin)
	if err != nil {
		return kopiaS3Capabilities{}, err
	}
	if err := ctx.Err(); err != nil {
		return kopiaS3Capabilities{}, err
	}
	entry.result = &result
	return result, nil
}

func probeKopiaS3Capabilities(ctx context.Context, bin string) (kopiaS3Capabilities, error) {
	res, err := runProcessWithTimeout(
		ctx, 10*time.Second, bin,
		[]string{"repository", "create", "s3", "--help"}, nil, "",
	)
	if err != nil {
		if ctx.Err() != nil {
			return kopiaS3Capabilities{}, ctx.Err()
		}
		// Never return subprocess output: even failed CLI initialization may
		// print environment or configuration details.
		slog.WarnContext(ctx, "kopia_s3_capability_probe_failed", "exit_code", res.ExitCode, "error", err)
		if errors.Is(err, context.DeadlineExceeded) {
			return kopiaS3Capabilities{}, fmt.Errorf("Kopia S3 capability check timed out; retry the operation: %w", err)
		}
		if res.ExitCode != 0 {
			return kopiaS3Capabilities{}, fmt.Errorf("Kopia S3 capability check failed (exit code %d); check the Agent logs and retry", res.ExitCode)
		}
		return kopiaS3Capabilities{}, fmt.Errorf("Kopia S3 capability check failed to run; check Agent access to the Kopia executable and retry")
	}
	help := res.Stdout + "\n" + res.Stderr
	return kopiaS3Capabilities{
		urlStyle:        strings.Contains(help, "--url-style"),
		credentialsFile: strings.Contains(help, "--credentials-file"),
	}, nil
}

func requireKopiaRefreshableS3Credentials(ctx context.Context, bin string) error {
	capabilities, err := managedKopiaS3Capabilities.load(ctx, bin)
	if err != nil {
		return err
	}
	if !capabilities.credentialsFile {
		return fmt.Errorf("Kopia does not support refreshable S3 credentials; update the Kopia artifact")
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
