//go:build !linux

package engine

import "errors"

func removeReconciledWorkspaceEntry(_, _ string, _ func()) error {
	return errors.New("managed workspace reconciliation requires Linux")
}
