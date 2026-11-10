//go:build !darwin

package kopialayout

import (
	"context"

	gprocess "github.com/shirou/gopsutil/v4/process"
)

func processEnvironment(ctx context.Context, p *gprocess.Process) ([]string, error) {
	return p.EnvironWithContext(ctx)
}
