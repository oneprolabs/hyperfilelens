//go:build !linux

package engine

import (
	"context"
	"errors"
)

func checkChatUpdateDiskHeadroom(_ context.Context, _ string, _ int64) error {
	return errors.New("Chat data update disk admission requires Linux")
}
