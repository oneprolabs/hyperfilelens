//go:build !linux && !windows

package enroll

func ensureUserSystemdUnit() error {
	return nil
}
