//go:build !linux && !windows

package performance

import (
	"context"
	"hyperfilelens/agent/internal/model"
)

func applyCPU(_ context.Context, _ *model.AgentConfig, _ int) Item {
	return Item{"unsupported", "This platform has no verified shared process-tree CPU quota; work remains unrestricted."}
}

func ProcessStarted(_ int) {}

func clearCPU(context.Context, *model.AgentConfig) Item {
	return Item{"disabled", "CPU governance disabled; this platform has no verified shared CPU quota."}
}
