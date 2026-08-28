package install

import "errors"

var errDetachedRunnerMayOwnFiles = errors.New("detached lifecycle runner may own staged files")

// ShouldRetainDetachedLifecycleFiles reports whether a failed launcher may
// still start later and therefore needs its staged script and package intact.
func ShouldRetainDetachedLifecycleFiles(err error) bool {
	return errors.Is(err, errDetachedRunnerMayOwnFiles)
}
