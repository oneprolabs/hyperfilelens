package enrollmentclient

import (
	"context"
	"crypto/x509"
	"errors"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"testing"

	"golang.org/x/sys/windows"
)

func TestNativeWindowsConnectionErrorClassification(t *testing.T) {
	for _, test := range []struct {
		name string
		err  error
		want bool
	}{
		{"reset", windows.WSAECONNRESET, true},
		{"refused", windows.WSAECONNREFUSED, true},
		{"access_denied", windows.WSAEACCES, false},
		{"invalid_argument", windows.WSAEINVAL, false},
		{"canceled", context.Canceled, false},
		{"certificate", x509.UnknownAuthorityError{}, false},
		{"configuration", errors.New("invalid configuration"), false},
	} {
		t.Run(test.name, func(t *testing.T) {
			wrapped := &RegistrationError{Cause: &url.Error{
				Op:  "Post",
				URL: "https://console/?token=private",
				Err: &net.OpError{
					Op: "dial", Net: "tcp",
					Err: &os.SyscallError{Syscall: "connectex", Err: test.err},
				},
			}}
			for _, err := range []error{test.err, wrapped} {
				if got := IsRetryableRegistrationError(err); got != test.want {
					t.Errorf("retryable=%v want=%v for %T", got, test.want, err)
				}
			}
			if strings.Contains(wrapped.Error(), "private") {
				t.Error("transport error exposed a credential-bearing URL")
			}
		})
	}
}

func TestWindowsConnectionErrorDoesNotOverrideHTTPStatusOrCancellation(t *testing.T) {
	for _, status := range []int{400, 401, 403, 409, 422, 500} {
		err := &RegistrationError{StatusCode: status, Cause: windows.WSAECONNRESET}
		if IsRetryableRegistrationError(err) {
			t.Errorf("HTTP %d must not be retried", status)
		}
	}
	if IsRetryableRegistrationError(errors.Join(context.Canceled, windows.WSAECONNRESET)) {
		t.Error("explicit cancellation must stop retries")
	}
	if !IsRetryableRegistrationError(&RegistrationError{StatusCode: http.StatusBadGateway}) {
		t.Error("HTTP 502 retry classification changed")
	}
}
