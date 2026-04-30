package services

import (
	"path/filepath"
	"testing"

	"easy_protocol/config"
)

func TestRuntimeStateStoreSaveAndLoad(t *testing.T) {
	tempDir := t.TempDir()
	store := NewRuntimeStateStore(true, filepath.Join(tempDir, "runtime-state.json"))

	original := RuntimeState{
		Policy: RuntimePolicyState{
			Global: RuntimeGlobalPolicyState{
				FallbackOnRetryableErrors: true,
				MaxFallbackAttempts:       2,
				RetryableCategories:       []string{"transport_error"},
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

	if err := store.Save(original); err != nil {
		t.Fatalf("save failed: %v", err)
	}
	loaded, err := store.Load()
	if err != nil {
		t.Fatalf("load failed: %v", err)
	}
	if !loaded.Policy.Global.FallbackOnRetryableErrors || loaded.Policy.Global.MaxFallbackAttempts != 2 {
		t.Fatalf("unexpected loaded global state: %#v", loaded.Policy.Global)
	}
	if loaded.Services["JSProtocol"].Enabled {
		t.Fatalf("expected JSProtocol to remain disabled after load")
	}
	if loaded.Policy.Operations["protocol.query.encode"].PreferredServices[0] != "GolangProtocol" {
		t.Fatalf("unexpected preferred services after load: %#v", loaded.Policy.Operations)
	}
}
