//go:build windows

package engine

import (
	"context"
	"testing"

	"hyperfilelens/agent/internal/model"
)

func TestWindowsUNCPolicyUsesNeutralAccessGuidance(t *testing.T) {
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{
		InstallationMode: model.InstallationModeUser,
	}})
	for _, kind := range []string{"browse", "path.info"} {
		for _, path := range []string{`\\server\share`, `\\?\UNC\server\share\folder`} {
			result := engine.Run(context.Background(), Command{
				Kind: kind, Payload: map[string]any{"path": path},
			}, nil)
			if result.Status != "failed" || result.Result["error_code"] != pathPermissionDeniedErrorCode {
				t.Fatalf("UNC policy %q %q must use neutral access guidance: %#v", kind, path, result)
			}
		}
	}
}
