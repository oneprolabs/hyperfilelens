//go:build !windows

package enroll

// DisableConsoleQuickEdit is a no-op outside Windows.
func DisableConsoleQuickEdit() func() { return func() {} }
