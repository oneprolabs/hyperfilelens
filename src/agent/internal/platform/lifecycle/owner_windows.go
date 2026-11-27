//go:build windows

package lifecycle

import "os"

// Windows uses the installation directory's ACL, rather than POSIX mode bits.
func checkOwner(_ os.FileInfo, _ bool, _ int64) error { return nil }

// atomicfile.Write uses MoveFileEx with WRITE_THROUGH on Windows.
func CanVerifyRuntimeOwner(_ int64) bool { return false }

func syncDirectory(path string) error { return nil }
