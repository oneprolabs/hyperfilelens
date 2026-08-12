package process

import (
	"bytes"
	"context"
	"strings"
	"testing"
)

func TestCaptureProgressLinesSplitsOnCarriageReturn(t *testing.T) {
	reader := strings.NewReader("first\rsecond\rthird\n")
	var lines []string
	captureProgressLines(reader, true, &bytes.Buffer{}, func(line string, _ bool) {
		lines = append(lines, line)
	})
	if len(lines) != 3 {
		t.Fatalf("expected 3 progress segments, got %d: %#v", len(lines), lines)
	}
	if lines[0] != "first" || lines[1] != "second" || lines[2] != "third" {
		t.Fatalf("unexpected segments: %#v", lines)
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
}
