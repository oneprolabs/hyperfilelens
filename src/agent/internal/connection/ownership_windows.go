//go:build windows

package connection

import (
	"os"

	"github.com/shirou/gopsutil/v4/process"
)

func pidStillExists(pid int32) bool {
	exists, err := process.PidExists(pid)
	return err != nil || exists
}

func fileOwner(os.FileInfo) (int, int)    { return -1, -1 }
func restoreOwner(string, int, int) error { return nil }
