//go:build !windows

package vfs

import (
	"os"
	"strings"
)

func resolveUserScopedPath(path string, allowMissing bool) (string, error) {
	if strings.EqualFold(strings.TrimSpace(os.Getenv("HFL_INSTALLATION_MODE")), "account") {
		return resolveAccountScopedPath(path, allowMissing)
	}
	return resolveHomeScopedPath(path, allowMissing)
}
