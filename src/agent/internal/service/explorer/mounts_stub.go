//go:build !windows

package explorer

func mountPointAllowed(_ string, _ bool) bool {
	return true
}

func extraMountPoints(seen map[string]struct{}) []string {
	_ = seen
	return nil
}
