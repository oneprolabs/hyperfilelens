package enrollmentclient

import (
	"errors"

	"golang.org/x/sys/windows"
)

func isRetryablePlatformConnectionError(err error) bool {
	// Winsock returns native error codes, not Go's synthetic Windows
	// syscall.ECONNRESET/ECONNREFUSED values. Match through HTTP/net wrappers.
	return errors.Is(err, windows.WSAECONNRESET) ||
		errors.Is(err, windows.WSAECONNREFUSED)
}
