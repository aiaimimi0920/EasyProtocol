package services

import (
	"testing"

	"easy_protocol/config"
)

func TestDiffRuntimeStatesReportsGlobalOperationAndServiceChanges(t *testing.T) {
	before := RuntimeState{
		Policy: RuntimePolicyState{
			Global: RuntimeGlobalPolicyState{
				FallbackOnRetryableErrors: true,
				MaxFallbackAttempts:       3,
			},
			Operations: map[string]RuntimeOperationPolicyState{
				"protocol.query.encode": {
					PreferredServices: []string{"JSProtocol", "GolangProtocol"},
				},
			},
		},
		Services: map[string]RuntimeServiceState{
			"JSProtocol": {Enabled: true},
		},
	}
	after := RuntimeState{
		Policy: RuntimePolicyState{
			Global: RuntimeGlobalPolicyState{
				FallbackOnRetryableErrors: false,
				MaxFallbackAttempts:       1,
			},
			Operations: map[string]RuntimeOperationPolicyState{
				"protocol.query.encode": {
					PreferredServices: []string{"GolangProtocol", "JSProtocol"},
					Policy: config.OperationPolicyConfig{
						FallbackMode:        "disabled",
						MaxFallbackAttempts: 1,
					},
				},
			},
		},
		Services: map[string]RuntimeServiceState{
			"JSProtocol": {Enabled: false},
		},
	}

	diff := DiffRuntimeStates(before, after)
	if _, ok := diff["global"]; !ok {
		t.Fatalf("expected global diff, got %#v", diff)
	}
	if _, ok := diff["operations"]; !ok {
		t.Fatalf("expected operation diff, got %#v", diff)
	}
	if _, ok := diff["services"]; !ok {
		t.Fatalf("expected service diff, got %#v", diff)
	}
}
