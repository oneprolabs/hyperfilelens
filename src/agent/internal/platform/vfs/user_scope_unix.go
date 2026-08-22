//go:build !windows

package vfs

func resolveUserScopedPath(path string, allowMissing bool) (string, error) {
	return resolveHomeScopedPath(path, allowMissing)
}
