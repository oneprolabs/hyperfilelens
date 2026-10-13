//go:build !windows

package connection

import (
	"os"
	"syscall"
)

func pidStillExists(pid int32) bool {
	return syscall.Kill(int(pid), 0) != syscall.ESRCH
}

func fileOwner(info os.FileInfo) (int, int) {
	if st, ok := info.Sys().(*syscall.Stat_t); ok {
		return int(st.Uid), int(st.Gid)
	}
	return -1, -1
}
func restoreOwner(path string, uid, gid int) error {
	if uid < 0 {
		return nil
	}
	return os.Chown(path, uid, gid)
}
