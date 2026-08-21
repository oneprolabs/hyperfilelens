package kopia

import (
	"sync"
	"time"
)

const (
	speedWindow       = 4 * time.Second
	speedMinSpan      = 1 * time.Second
	speedFreshness    = 6 * time.Second
	speedWindowSource = "window"
)

type speedSample struct {
	counter int64
	at      time.Time
}

// SpeedTracker computes bounded-window throughput from exact counter samples.
type SpeedTracker struct {
	mu          sync.Mutex
	samples     []speedSample
	lastSpeed   int64
	lastSpeedAt time.Time
}

// Observe returns a speed only after the samples span enough time. A non-empty
// source makes a measured zero distinct from an unavailable sample.
func (t *SpeedTracker) Observe(counter int64, now time.Time) (speedBps int64, source string) {
	if counter < 0 || now.IsZero() {
		return 0, ""
	}

	t.mu.Lock()
	defer t.mu.Unlock()

	if len(t.samples) == 0 || counter < t.samples[len(t.samples)-1].counter {
		t.samples = []speedSample{{counter: counter, at: now}}
		t.lastSpeed = 0
		t.lastSpeedAt = time.Time{}
		return 0, ""
	}

	last := t.samples[len(t.samples)-1]
	if !now.After(last.at) {
		if now.Equal(last.at) {
			t.samples[len(t.samples)-1].counter = counter
		}
		return 0, ""
	}

	t.samples = append(t.samples, speedSample{counter: counter, at: now})
	for len(t.samples) > 2 && now.Sub(t.samples[1].at) >= speedWindow {
		t.samples = t.samples[1:]
	}

	oldest := t.samples[0]
	span := now.Sub(oldest.at)
	if span < speedMinSpan {
		if !t.lastSpeedAt.IsZero() && now.Sub(t.lastSpeedAt) <= speedFreshness {
			return t.lastSpeed, speedWindowSource
		}
		return 0, ""
	}

	delta := counter - oldest.counter
	if delta < 0 {
		return 0, ""
	}

	t.lastSpeed = int64(float64(delta) / span.Seconds())
	t.lastSpeedAt = now
	return t.lastSpeed, speedWindowSource
}

// ProcessingCounter returns logical bytes processed by Kopia.
func ProcessingCounter(snapshot ProgressSnapshot) int64 {
	if snapshot.ProcessedBytes > 0 {
		return snapshot.ProcessedBytes
	}
	return snapshot.CachedBytes + snapshot.HashedBytes
}

// SpeedCounter preserves the legacy helper name while returning logical work.
func SpeedCounter(snapshot ProgressSnapshot) int64 {
	return ProcessingCounter(snapshot)
}
