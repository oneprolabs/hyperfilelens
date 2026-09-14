//go:build windows

package enroll

import "golang.org/x/sys/windows"

const (
	enableQuickEditMode = 0x0040
	enableExtendedFlags = 0x0080
)

// DisableConsoleQuickEdit prevents a click in a Windows console from
// entering mark mode, which otherwise blocks writes until Enter or Escape.
// It is intentionally best-effort because enrollment may run without a
// console (for example from a scheduled task or redirected standard input).
// The returned function restores the original mode for the caller's console.
func DisableConsoleQuickEdit() func() {
	handle, err := windows.GetStdHandle(windows.STD_INPUT_HANDLE)
	if err != nil || handle == 0 || handle == windows.InvalidHandle {
		return func() {}
	}
	var mode uint32
	if err := windows.GetConsoleMode(handle, &mode); err != nil {
		return func() {}
	}
	originalMode := mode
	mode &^= enableQuickEditMode
	mode |= enableExtendedFlags
	if err := windows.SetConsoleMode(handle, mode); err != nil {
		return func() {}
	}
	return func() { _ = windows.SetConsoleMode(handle, originalMode) }
}
