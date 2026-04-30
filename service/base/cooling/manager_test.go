package cooling

import (
	"testing"
	"time"
)

func TestManagerCoolsAfterThreshold(t *testing.T) {
	now := time.Date(2026, 3, 31, 0, 0, 0, 0, time.UTC)
	manager := New(2, time.Minute)

	state := manager.RecordFailure("JSProtocol", "transport_error", true, now)
	if state.Cooled {
		t.Fatalf("service should not be cooled after first failure")
	}

	state = manager.RecordFailure("JSProtocol", "transport_error", true, now.Add(time.Second))
	if !state.Cooled {
		t.Fatalf("service should be cooled after threshold is reached")
	}

	snapshot := manager.Snapshot("JSProtocol", now.Add(2*time.Minute))
	if snapshot.Cooled {
		t.Fatalf("service cooldown should auto-clear after duration")
	}
}
