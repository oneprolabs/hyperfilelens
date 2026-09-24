package app

import (
	"testing"

	"hyperfilelens/agent/internal/model"
)

func TestHeartbeatPayloadIncludesCapabilities(t *testing.T) {
	agent := &Agent{
		storageInventory: map[string]any{"disk_count": 1},
	}

	payload := agent.heartbeatPayload()
	if payload["disk_count"] != 1 {
		t.Fatalf("heartbeat payload lost storage inventory: %#v", payload)
	}
	capabilities, ok := payload["capabilities"].([]string)
	if !ok {
		t.Fatalf("capabilities type = %T, want []string", payload["capabilities"])
	}
	for _, capability := range capabilities {
		if capability == "insight_safe_restore_v1" {
			return
		}
	}
	t.Fatalf("heartbeat capabilities = %v, missing insight_safe_restore_v1", capabilities)
}

func TestDurableNodeIdentity(t *testing.T) {
	for _, test := range []struct {
		name string
		cfg  *model.AgentConfig
		want bool
	}{
		{name: "durable", cfg: &model.AgentConfig{NodeID: "42", NodeToken: "hfln_credential"}, want: true},
		{name: "legacy credential", cfg: &model.AgentConfig{NodeID: "42", NodeToken: "legacy"}},
		{name: "missing node", cfg: &model.AgentConfig{NodeToken: "hfln_credential"}},
		{name: "nil config", cfg: nil},
	} {
		t.Run(test.name, func(t *testing.T) {
			if got := durableNodeIdentity(test.cfg); got != test.want {
				t.Fatalf("durableNodeIdentity() = %t, want %t", got, test.want)
			}
		})
	}
}
