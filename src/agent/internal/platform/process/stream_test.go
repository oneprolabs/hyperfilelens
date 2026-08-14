package process

import (
	"context"
	"os"
	"strings"
	"testing"
	"time"
)

func TestCaptureProgressLinesSplitsOnCarriageReturn(t *testing.T) {
	reader := strings.NewReader("first\rsecond\rthird\n")
	var lines []string
	captureProgressLines(reader, true, newBoundedTailBuffer(streamStderrLimit), func(line string, _ bool) {
		lines = append(lines, line)
	})
	if len(lines) != 3 {
		t.Fatalf("expected 3 progress segments, got %d: %#v", len(lines), lines)
	}
	if lines[0] != "first" || lines[1] != "second" || lines[2] != "third" {
		t.Fatalf("unexpected segments: %#v", lines)
	}
}

func TestBoundedTailBufferKeepsMemoryBoundedForLargeOutput(t *testing.T) {
	buf := newBoundedTailBuffer(streamStderrLimit)
	chunk := []byte(strings.Repeat("x", 1024*1024))
	for range 300 {
		if _, err := buf.Write(chunk); err != nil {
			t.Fatal(err)
		}
	}
	if len(buf.data) != streamStderrLimit {
		t.Fatalf("retained bytes = %d, want %d", len(buf.data), streamStderrLimit)
	}
	if buf.TotalBytes() != 300*1024*1024 || !buf.Truncated() {
		t.Fatalf("total=%d truncated=%v", buf.TotalBytes(), buf.Truncated())
	}
}

func TestCaptureProgressLinesCapsLongLine(t *testing.T) {
	input := strings.Repeat("a", streamLineLimit*4) + "\n"
	buf := newBoundedTailBuffer(streamStderrLimit)
	var got string
	captureProgressLines(strings.NewReader(input), true, buf, func(line string, _ bool) {
		got = line
	})
	if len(got) != streamLineLimit {
		t.Fatalf("callback line bytes = %d, want %d", len(got), streamLineLimit)
	}
	if !buf.Truncated() || buf.TotalBytes() != int64(len(input)) {
		t.Fatalf("total=%d truncated=%v", buf.TotalBytes(), buf.Truncated())
	}
}

func TestCaptureProgressLinesReportsExactRawByteCount(t *testing.T) {
	input := "first\r\nsecond\rlast"
	buf := newBoundedTailBuffer(streamStderrLimit)
	captureProgressLines(strings.NewReader(input), true, buf, nil)

	if buf.TotalBytes() != int64(len(input)) {
		t.Fatalf("total=%d, want %d", buf.TotalBytes(), len(input))
	}
	if buf.String() != "first\nsecond\nlast" {
		t.Fatalf("captured output = %q", buf.String())
	}
}

func TestCaptureProgressLinesHandlesInvalidUTF8(t *testing.T) {
	input := []byte{'o', 'k', 0xff, 0xfe, '\n'}
	buf := newBoundedTailBuffer(streamStderrLimit)
	var got string
	captureProgressLines(strings.NewReader(string(input)), true, buf, func(line string, _ bool) {
		got = line
	})

	if len(got) != len(input)-1 || buf.TotalBytes() != int64(len(input)) {
		t.Fatalf("callback_bytes=%d total=%d", len(got), buf.TotalBytes())
	}
}

func TestRunStreamingEmitsCarriageReturnProgress(t *testing.T) {
	ctx := context.Background()
	var lines []string
	res, err := RunStreaming(
		ctx,
		"bash",
		[]string{"-c", `printf 'phase1\rphase2\rphase3\n' 1>&2`},
		nil,
		"",
		func(line string, stderr bool) {
			if stderr {
				lines = append(lines, line)
			}
		},
	)
	if err != nil {
		t.Fatalf("RunStreaming failed: %v", err)
	}
	if len(lines) != 3 {
		t.Fatalf("expected 3 stderr progress lines, got %d: %#v", len(lines), lines)
	}
	if !strings.Contains(res.Stderr, "phase3") {
		t.Fatalf("expected captured stderr, got %q", res.Stderr)
	}
}

func TestRunStreamingDiscardStdoutStillEmitsLinesAndCapturesStderr(t *testing.T) {
	ctx := context.Background()
	var stdoutLines []string
	res, err := RunStreamingDiscardStdout(
		ctx,
		"bash",
		[]string{"-c", `printf 'one\ntwo\n'; printf 'warning\n' 1>&2`},
		nil,
		"",
		func(line string, stderr bool) {
			if !stderr {
				stdoutLines = append(stdoutLines, line)
			}
		},
	)
	if err != nil {
		t.Fatalf("RunStreamingDiscardStdout failed: %v", err)
	}
	if res.Stdout != "" {
		t.Fatalf("expected discarded stdout, got %q", res.Stdout)
	}
	if strings.Join(stdoutLines, ",") != "one,two" {
		t.Fatalf("unexpected streamed stdout lines: %#v", stdoutLines)
	}
	if res.Stderr != "warning" {
		t.Fatalf("expected captured stderr, got %q", res.Stderr)
	}
	if res.StdoutTotalBytes == 0 || !res.StdoutTruncated {
		t.Fatalf("discarded stdout metadata = total:%d truncated:%v", res.StdoutTotalBytes, res.StdoutTruncated)
	}
}

func TestRunCancellationKillsDescendantProcessGroup(t *testing.T) {
	if _, err := os.Stat("/bin/bash"); err != nil {
		t.Skip("bash is required for the Unix process-group contract")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	started := time.Now()

	_, err := Run(
		ctx,
		"bash",
		[]string{"-c", "sleep 30 & wait"},
		nil,
		"",
	)

	if err == nil || ctx.Err() == nil {
		t.Fatalf("Run error = %v, context error = %v; want cancellation", err, ctx.Err())
	}
	if elapsed := time.Since(started); elapsed > 5*time.Second {
		t.Fatalf("process group shutdown took %s", elapsed)
	}
}
