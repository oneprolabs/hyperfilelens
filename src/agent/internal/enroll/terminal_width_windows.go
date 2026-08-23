//go:build windows

package enroll

import (
	"os"

	"golang.org/x/sys/windows"
)

func nativeTerminalColumns(file *os.File) int {
	var info windows.ConsoleScreenBufferInfo
	if err := windows.GetConsoleScreenBufferInfo(windows.Handle(file.Fd()), &info); err != nil {
		return 0
	}
	width := int(info.Window.Right - info.Window.Left + 1)
	if width < 40 {
		return 0
	}
	return width
}
