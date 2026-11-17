package kopialayout

import "errors"

var errOutsideRuntime = errors.New("private runtime path is outside the managed Kopia runtime")

// PreparePrivateRuntimeDir applies the same symlink and OS ACL protections used
// for Kopia metadata to task-local runtime identities.
func PreparePrivateRuntimeDir(root, dir string) error {
	if !within(RuntimeDir(root), dir) {
		return errOutsideRuntime
	}
	return privateDir(root, dir)
}

// WritePrivateRuntimeFile uses atomic replacement and owner/administrator-only
// permissions on both Unix and Windows. Callers own the task lifetime and remove
// the file when execution ends.
func WritePrivateRuntimeFile(root, path string, data []byte) error {
	if !within(RuntimeDir(root), path) {
		return errOutsideRuntime
	}
	return writeDurable(root, path, data)
}
