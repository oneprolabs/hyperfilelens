package engine

import "context"

func withLensWorkspaceLock(
	ctx context.Context,
	lockPath string,
	action func() error,
) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	unlock, err := acquireLensWorkspaceFileLock(ctx, lockPath)
	if err != nil {
		return err
	}
	defer unlock()
	if err := ctx.Err(); err != nil {
		return err
	}
	return action()
}
