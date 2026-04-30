package services

import (
	"path/filepath"
	"testing"
	"time"
)

func TestControlPlaneStateStoreSaveAndLoad(t *testing.T) {
	tempDir := t.TempDir()
	store := NewControlPlaneStateStore(true, filepath.Join(tempDir, "state", "control-plane-state.json"))

	expected := ControlPlaneState{
		ReadToken:             "read-secret",
		MutateToken:           "mutate-secret",
		PreviousReadToken:     "read-old",
		PreviousMutateToken:   "mutate-old",
		ReadTokenGraceUntil:   time.Date(2026, 4, 1, 10, 15, 0, 0, time.UTC),
		MutateTokenGraceUntil: time.Date(2026, 4, 1, 10, 20, 0, 0, time.UTC),
		FreezeEnabled:         true,
		MaintenanceEnabled:    true,
		MaintenanceReason:     "planned maintenance",
		MaintenanceETA:        time.Date(2026, 4, 1, 10, 0, 0, 0, time.UTC),
		MaintenanceStartedAt:  time.Date(2026, 4, 1, 9, 30, 0, 0, time.UTC),
		LastMaintenanceSummary: &MaintenanceCompletionSummary{
			StartedAt:         time.Date(2026, 4, 1, 8, 45, 0, 0, time.UTC),
			CompletedAt:       time.Date(2026, 4, 1, 9, 0, 0, 0, time.UTC),
			DurationSeconds:   900,
			Reason:            "previous maintenance",
			ETA:               time.Date(2026, 4, 1, 9, 5, 0, 0, time.UTC),
			PublicRejectCount: 4,
			TopRejectedOperations: []MaintenanceOperationRejectCount{
				{Operation: "protocol.query.encode", Count: 3},
				{Operation: "protocol.regex.extract", Count: 1},
			},
		},
	}
	if err := store.Save(expected); err != nil {
		t.Fatalf("save control-plane state failed: %v", err)
	}

	loaded, err := store.Load()
	if err != nil {
		t.Fatalf("load control-plane state failed: %v", err)
	}
	if loaded.ReadToken != expected.ReadToken ||
		loaded.MutateToken != expected.MutateToken ||
		loaded.PreviousReadToken != expected.PreviousReadToken ||
		loaded.PreviousMutateToken != expected.PreviousMutateToken ||
		!loaded.ReadTokenGraceUntil.Equal(expected.ReadTokenGraceUntil) ||
		!loaded.MutateTokenGraceUntil.Equal(expected.MutateTokenGraceUntil) ||
		loaded.FreezeEnabled != expected.FreezeEnabled ||
		loaded.MaintenanceEnabled != expected.MaintenanceEnabled ||
		loaded.MaintenanceReason != expected.MaintenanceReason ||
		!loaded.MaintenanceETA.Equal(expected.MaintenanceETA) ||
		!loaded.MaintenanceStartedAt.Equal(expected.MaintenanceStartedAt) {
		t.Fatalf("unexpected control-plane state: %#v", loaded)
	}
	if loaded.LastMaintenanceSummary == nil {
		t.Fatalf("expected LastMaintenanceSummary to persist, got %#v", loaded)
	}
	if loaded.LastMaintenanceSummary.PublicRejectCount != expected.LastMaintenanceSummary.PublicRejectCount {
		t.Fatalf("unexpected persisted maintenance summary: %#v", loaded.LastMaintenanceSummary)
	}
	if len(loaded.LastMaintenanceSummary.TopRejectedOperations) != len(expected.LastMaintenanceSummary.TopRejectedOperations) {
		t.Fatalf("unexpected persisted maintenance operations: %#v", loaded.LastMaintenanceSummary)
	}
	if loaded.UpdatedAt.IsZero() {
		t.Fatalf("expected UpdatedAt to be set")
	}
}
