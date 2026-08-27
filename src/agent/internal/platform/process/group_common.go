package process

import (
	"context"
	"fmt"
	"os/exec"
	"sync"
)

func startContextProcessGroupKill(ctx context.Context, cmd *exec.Cmd) (func(), error) {
	releaseLifetime, err := bindProcessLifetime(cmd.Process)
	if err != nil {
		return nil, fmt.Errorf("bind child process lifetime: %w", err)
	}
	if ctx == nil {
		return releaseLifetime, nil
	}
	done := make(chan struct{})
	var closeOnce sync.Once
	go func() {
		select {
		case <-ctx.Done():
			if cmd.Process != nil {
				_ = killProcessGroup(context.Background(), cmd.Process)
			}
		case <-done:
		}
	}()
	return func() {
		closeOnce.Do(func() {
			close(done)
			releaseLifetime()
		})
	}, nil
}
