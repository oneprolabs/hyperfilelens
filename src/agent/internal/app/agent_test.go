package app

import (
	"testing"

	"hyperfilelens/agent/internal/model"
)

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
