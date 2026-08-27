package wire

import (
	"context"
	"testing"
	"time"

	"hyperfilelens/agent/internal/controller"
)

type channelSender struct {
	frames chan any
}

func (s *channelSender) SendJSON(_ context.Context, frame any) error {
	s.frames <- frame
	return nil
}

func TestPreparedSnapshotWaitsForSchedulerSlot(t *testing.T) {
	scheduler := controller.NewScheduler(1)
	releaseOccupied, ok := scheduler.TryAcquire()
	if !ok {
		t.Fatal("failed to occupy scheduler slot")
	}
	defer releaseOccupied()

	handler := NewHandler(nil, controller.NewTracker(), nil, scheduler)
	sender := &channelSender{frames: make(chan any, 4)}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		handler.runTask(ctx, sender, &TaskCommand{
			TaskID: "queued-snapshot",
			Kind:   "backup.snapshot.create",
		})
		close(done)
	}()

	select {
	case raw := <-sender.frames:
		progress, ok := raw.(TaskProgress)
		if !ok {
			t.Fatalf("first frame type = %T, want TaskProgress", raw)
		}
		if progress.Progress["kopia_phase"] != "waiting_for_snapshot_slot" {
			t.Fatalf("unexpected queued progress: %#v", progress.Progress)
		}
	case <-time.After(time.Second):
		t.Fatal("queued progress was not sent")
	}

	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("queued snapshot did not stop after cancellation")
	}
}

func TestPathSizeReportsBusyWithoutWaitingForSchedulerSlot(t *testing.T) {
	scheduler := controller.NewScheduler(1)
	releaseOccupied, ok := scheduler.TryAcquire()
	if !ok {
		t.Fatal("failed to occupy path-size scheduler slot")
	}
	defer releaseOccupied()

	handler := NewHandler(
		nil,
		controller.NewTracker(),
		nil,
		controller.NewScheduler(1),
		scheduler,
	)
	sender := &channelSender{frames: make(chan any, 2)}
	done := make(chan struct{})
	go func() {
		handler.runTask(context.Background(), sender, &TaskCommand{
			TaskID: "busy-path-size",
			Kind:   "path.size",
			NodeID: 1,
		})
		close(done)
	}()

	select {
	case raw := <-sender.frames:
		result, ok := raw.(TaskResult)
		if !ok {
			t.Fatalf("result frame type = %T, want TaskResult", raw)
		}
		if result.Status != "failed" || result.Result["error_code"] != "PATH_SIZE_BUSY" {
			t.Fatalf("unexpected busy result: %#v", result)
		}
	case <-time.After(time.Second):
		t.Fatal("busy path.size result was not sent")
	}

	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("busy path.size task waited for scheduler slot")
	}
}
