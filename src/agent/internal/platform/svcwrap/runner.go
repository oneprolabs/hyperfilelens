package svcwrap

import (
	"context"
	"errors"

	"github.com/kardianos/service"
)

// Kept platform-independent so the service lifecycle can be tested without SCM.
type daemonRunner struct {
	run       func(context.Context) error
	onFailure func(error)
	cancel    context.CancelFunc
	done      chan struct{}
}

func (d *daemonRunner) Start(service.Service) error {
	ctx, cancel := context.WithCancel(context.Background())
	d.cancel = cancel
	d.done = make(chan struct{})
	go func() {
		defer close(d.done)
		err := d.run(ctx)
		// The composition root converts context.Canceled to nil. A requested
		// stop is normal regardless of that conversion or wrapped cancellation.
		if ctx.Err() != nil {
			return
		}
		if err == nil {
			err = errors.New("agent service stopped unexpectedly")
		}
		d.onFailure(err)
	}()
	return nil
}

func (d *daemonRunner) Stop(service.Service) error {
	if d.cancel != nil {
		d.cancel()
	}
	if d.done != nil {
		<-d.done
	}
	return nil
}
