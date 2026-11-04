package enrollmentclient

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"syscall"
	"time"
)

// RegistrationError describes a heartbeat failure without exposing
// enrollment credentials. HTTP status is zero for transport failures.
type RegistrationError struct {
	StatusCode int
	Operation  string
	Detail     string
	RetryAfter time.Duration
	Cause      error
}

func (e *RegistrationError) Error() string {
	if e == nil {
		return ""
	}
	operation := e.Operation
	if operation == "" {
		operation = "heartbeat"
	}
	if e.StatusCode != 0 {
		message := fmt.Sprintf("%s HTTP %d %s", operation, e.StatusCode, http.StatusText(e.StatusCode))
		if e.Detail != "" {
			message += ": " + e.Detail
		}
		return message
	}
	// Transport errors may contain credential-bearing URLs or proxy details.
	if errors.Is(e.Cause, context.DeadlineExceeded) {
		return operation + " request timed out"
	}
	return operation + " connection interrupted"
}

// Keep actionable protocol errors, not arbitrary proxy HTML/credential echoes.
func registrationErrorDetail(raw []byte) string {
	data, err := decodeAPIData(raw)
	if err != nil {
		return ""
	}
	// ProblemDetails uses a stable machine code, not a free-form error string.
	if stringField(data, "code") == "SUBSCRIPTION.QUOTA_EXCEEDED" {
		return "Organization quota is full."
	}
	detail, _ := data["error"].(string)
	switch detail {
	case "invalid enrollment token", "invalid or expired enrollment token", "invalid node credential",
		"organization not found", "X-Org-Key required",
		"installation mode does not match enrollment token",
		"installation mode authorization changed",
		"installation mode is fixed during enrollment",
		"installation routing identity does not match",
		"installation session expired or is no longer active",
		"installation session does not match this installation identity",
		"node not found; enrollment token required":
		return detail
	default:
		return ""
	}
}

func (e *RegistrationError) Unwrap() error {
	if e == nil {
		return nil
	}
	return e.Cause
}

// IsRetryableRegistrationError reports failures that may be caused by a
// transient control-plane or network condition. Authentication and protocol
// errors intentionally remain non-retryable.
func IsRetryableRegistrationError(err error) bool {
	if err == nil {
		return false
	}
	var registrationErr *RegistrationError
	if errors.As(err, &registrationErr) {
		switch registrationErr.StatusCode {
		case 408, 429, 502, 503, 504:
			return true
		case 0:
			break
		default:
			return false
		}
	}

	if errors.Is(err, context.Canceled) {
		return false
	}
	var networkErr net.Error
	if errors.As(err, &networkErr) && networkErr.Timeout() {
		return true
	}
	var dnsErr *net.DNSError
	if errors.As(err, &dnsErr) && dnsErr.Temporary() {
		return true
	}
	return errors.Is(err, syscall.ECONNRESET) || errors.Is(err, syscall.ECONNREFUSED) ||
		isRetryablePlatformConnectionError(err) ||
		errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF)
}

// IsInvalidEnrollmentToken reports heartbeat failures caused by an invalid token.
func IsInvalidEnrollmentToken(err error) bool {
	if err == nil {
		return false
	}
	var httpErr *RegistrationError
	if errors.As(err, &httpErr) {
		return httpErr.StatusCode == http.StatusUnauthorized
	}
	msg := strings.ToLower(err.Error())
	return strings.Contains(msg, "invalid enrollment token") ||
		strings.Contains(msg, "401") && strings.Contains(msg, "token")
}
