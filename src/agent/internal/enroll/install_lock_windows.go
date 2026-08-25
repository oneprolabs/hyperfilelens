//go:build windows

package enroll

import (
	"context"
	"errors"
	"fmt"

	"golang.org/x/sys/windows"

	"hyperfilelens/agent/internal/platform/vfs"
)

func acquireInstallLock(_ context.Context) (func(), error) {
	mutexName := `Global\HyperFileLensInstaller`
	if vfs.UserInstallation() {
		tokenUser, err := windows.GetCurrentProcessToken().GetTokenUser()
		if err != nil {
			return nil, fmt.Errorf("resolve current Windows user for installer lock: %w", err)
		}
		if tokenUser == nil || tokenUser.User.Sid == nil {
			return nil, fmt.Errorf("resolve current Windows user for installer lock: SID unavailable")
		}
		mutexName = `Global\HyperFileLensInstaller.User.` + tokenUser.User.Sid.String()
	}
	name, err := windows.UTF16PtrFromString(mutexName)
	if err != nil {
		return nil, err
	}
	handle, err := windows.CreateMutex(nil, false, name)
	if handle != 0 && errors.Is(err, windows.ERROR_ALREADY_EXISTS) {
		_ = windows.CloseHandle(handle)
		return nil, ErrInstallLocked
	}
	if err != nil {
		if handle != 0 {
			_ = windows.CloseHandle(handle)
		}
		return nil, err
	}
	return func() { _ = windows.CloseHandle(handle) }, nil
}
