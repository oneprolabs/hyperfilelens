//go:build windows

package kopialayout

import (
	"golang.org/x/sys/windows"
)

func restrictPath(path string, dir bool) error {
	token := windows.GetCurrentProcessToken()
	user, err := token.GetTokenUser()
	if err != nil {
		return err
	}
	flags := ""
	if dir {
		flags = "OICI"
	}
	sd, err := windows.SecurityDescriptorFromString(
		"D:P(A;" + flags + ";FA;;;SY)(A;" + flags + ";FA;;;BA)(A;" + flags + ";FA;;;" + user.User.Sid.String() + ")",
	)
	if err != nil {
		return err
	}
	acl, _, err := sd.DACL()
	if err != nil {
		return err
	}
	return windows.SetNamedSecurityInfo(path, windows.SE_FILE_OBJECT,
		windows.DACL_SECURITY_INFORMATION|windows.PROTECTED_DACL_SECURITY_INFORMATION,
		nil, nil, acl, nil)
}

func syncDir(_ string) error {
	// MoveFileEx with WRITE_THROUGH provides the durable replacement on Windows;
	// opening a directory and calling File.Sync is not supported there.
	return nil
}

func replaceDurable(source, target string) error {
	from, err := windows.UTF16PtrFromString(source)
	if err != nil {
		return err
	}
	to, err := windows.UTF16PtrFromString(target)
	if err != nil {
		return err
	}
	return windows.MoveFileEx(from, to, windows.MOVEFILE_REPLACE_EXISTING|windows.MOVEFILE_WRITE_THROUGH)
}
