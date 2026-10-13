//go:build windows

package connection

import "os"

func fileOwner(os.FileInfo) (int, int)    { return -1, -1 }
func restoreOwner(string, int, int) error { return nil }
