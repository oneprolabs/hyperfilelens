package logging

import (
	"errors"
	"log/slog"
	"strings"
	"testing"
)

func TestAttrsToMapRendersErrorsAsMessages(t *testing.T) {
	err := errors.New("cache layout migration failed")

	attrs := attrsToMap([]slog.Attr{slog.Any("err", err)})
	if got, ok := attrs["err"].(string); !ok || got != err.Error() {
		t.Fatalf("attrsToMap err = %#v, want %q", attrs["err"], err.Error())
	}
}

func TestBuildMessageDoesNotSerializeErrorsAsEmptyObjects(t *testing.T) {
	message := buildMessage("agent startup failed", []slog.Attr{
		slog.Any("err", errors.New("migration lock is busy")),
	})

	if !strings.Contains(message, `"err":"migration lock is busy"`) {
		t.Fatalf("buildMessage = %q, want rendered error text", message)
	}
	if strings.Contains(message, `"err":{}`) {
		t.Fatalf("buildMessage = %q, must not render an error as an empty object", message)
	}
}
