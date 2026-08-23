//go:build !windows

package enroll

import (
	"os"

	"golang.org/x/sys/unix"
)

func nativeTerminalColumns(file *os.File) int {
	size, err := unix.IoctlGetWinsize(int(file.Fd()), unix.TIOCGWINSZ)
	if err != nil || size == nil || size.Col < 40 {
		return 0
	}
	return int(size.Col)
}
