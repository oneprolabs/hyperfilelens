package svcwrap

import (
	"context"
	"errors"
	"fmt"
	"testing"
	"time"
)

func waitForRunner(t *testing.T, done <-chan struct{}) {
	t.Helper()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("service runner did not finish")
	}
}

func TestRequestedStopDoesNotReportFailure(t *testing.T) {
	for _, result := range []string{"nil", "canceled", "wrapped"} {
		t.Run(result, func(t *testing.T) {
			failures := make(chan error, 1)
			started := make(chan struct{})
			d := &daemonRunner{
				run: func(ctx context.Context) error {
					close(started)
					<-ctx.Done()
					switch result {
					case "canceled":
						return ctx.Err()
					case "wrapped":
						return fmt.Errorf("shutdown: %w", ctx.Err())
					default:
						return nil // The composition root's normal shutdown.
					}
				},
				onFailure: func(err error) { failures <- err },
			}
			if err := d.Start(nil); err != nil {
				t.Fatal(err)
			}
			waitForRunner(t, started)
			stopped := make(chan struct{})
			go func() {
				_ = d.Stop(nil)
				close(stopped)
			}()
			waitForRunner(t, stopped)
			_ = d.Stop(nil) // Repeated stops remain safe.
			select {
			case err := <-failures:
				t.Fatalf("normal stop reported failure: %v", err)
			default:
			}
		})
	}
}

func TestUnexpectedCompletionReportsFailureBeforeDone(t *testing.T) {
	for _, cause := range []error{nil, errors.New("migration failed"), context.Canceled} {
		failures := make(chan error, 1)
		d := &daemonRunner{
			run: func(context.Context) error { return cause },
			onFailure: func(err error) {
				failures <- err
			},
		}
		if err := d.Start(nil); err != nil {
			t.Fatal(err)
		}
		waitForRunner(t, d.done)
		select {
		case err := <-failures:
			if err == nil || (cause != nil && !errors.Is(err, cause)) {
				t.Errorf("failure=%v, cause=%v", err, cause)
			}
		default:
			t.Fatal("unexpected completion was not reported")
		}
		_ = d.Stop(nil)
	}
}
