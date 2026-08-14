//go:build windows

package enroll

import (
	"context"
	"fmt"
)

func RunGatewayUpgrade(_ context.Context, _ string) error {
	return fmt.Errorf("gateway-upgrade is Linux-only")
}

func RunGatewayUninstall(_ context.Context, _ bool) error {
	return fmt.Errorf("gateway-uninstall is Linux-only")
}

func runGatewayUninstall(_ context.Context, _ bool, _ bool) error {
	return fmt.Errorf("gateway-uninstall is Linux-only")
}
