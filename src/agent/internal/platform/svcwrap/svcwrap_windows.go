//go:build windows

package svcwrap

import (
	"context"
	"os"
	"time"

	"github.com/kardianos/service"

	"hyperfilelens/agent/internal/infra/logging"
)

const (
	serviceName        = "HyperFileLensAgent"
	serviceDisplayName = "HyperFileLens Agent"
	serviceDescription = "HyperFileLens backup agent (WebSocket control plane and local CLI)"
)

func exitOnFailure(err error) {
	// Await the critical diagnostic, but do not let broken log storage leave
	// an inert service marked Running indefinitely.
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_ = logging.ErrorAndFlush(ctx, "agent service stopped with error", err)
	// A failure exit lets SCM apply the existing recovery actions.
	os.Exit(1)
}

// RunIfService registers with Windows SCM when the process is started as a service.
func RunIfService(run func(context.Context) error) (handled bool, err error) {
	if service.Interactive() {
		return false, nil
	}
	svc, err := service.New(&daemonRunner{run: run, onFailure: exitOnFailure}, &service.Config{
		Name:        serviceName,
		DisplayName: serviceDisplayName,
		Description: serviceDescription,
	})
	if err != nil {
		return false, err
	}
	return true, svc.Run()
}
