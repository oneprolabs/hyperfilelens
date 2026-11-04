//go:build !windows

package enrollmentclient

func isRetryablePlatformConnectionError(err error) bool {
	return false
}
