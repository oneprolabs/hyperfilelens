package connection

import (
	"context"
	"errors"
)

func lockSidecar(context.Context) (func(), error) {
	return nil, errors.New("managed AI engine maintenance is Linux-only")
}
