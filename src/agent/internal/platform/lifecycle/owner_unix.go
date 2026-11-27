//go:build !windows

package lifecycle

import (
	"fmt"
	"os"
	"syscall"
)

func CanVerifyRuntimeOwner(uid int64) bool {
	return os.Geteuid() == 0 && uid > 0 && uid < 1<<32-1
}

func checkOwner(info os.FileInfo, trustedRoot bool, auditUID int64) error {
	stat, ok := info.Sys().(*syscall.Stat_t)
	if ok && !trustedRoot && CanVerifyRuntimeOwner(auditUID) && stat.Uid == uint32(auditUID) && info.Mode().Perm()&0022 == 0 {
		return nil
	}
	if !ok || !trustedOwner(stat.Uid, uint32(os.Geteuid()), trustedRoot, info.Mode()) {
		return fmt.Errorf("lifecycle path must be owned by the runtime principal and not writable by others")
	}
	return nil
}

func trustedOwner(owner, runtimeUID uint32, trustedRoot bool, mode os.FileMode) bool {
	return mode.Perm()&0022 == 0 && (owner == runtimeUID || (trustedRoot && owner == 0))
}

func syncDirectory(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	return f.Sync()
}
