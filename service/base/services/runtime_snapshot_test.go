package services

import (
	"path/filepath"
	"testing"
)

func TestRuntimeSnapshotStoreSaveLoadAndFetch(t *testing.T) {
	tempDir := t.TempDir()
	store := NewRuntimeSnapshotStore(true, filepath.Join(tempDir, "snapshots"), 10)

	summary, err := store.Save("policy_update", "tester", "unit test", RuntimeState{
		Services: map[string]RuntimeServiceState{
			"JSProtocol": {Enabled: false},
		},
	})
	if err != nil {
		t.Fatalf("save snapshot failed: %v", err)
	}
	if summary.ID == "" {
		t.Fatalf("expected snapshot id")
	}

	loaded := NewRuntimeSnapshotStore(true, filepath.Join(tempDir, "snapshots"), 10)
	if err := loaded.Load(); err != nil {
		t.Fatalf("load snapshot store failed: %v", err)
	}
	items := loaded.List(10)
	if len(items) != 1 {
		t.Fatalf("expected one snapshot summary, got %#v", items)
	}
	snapshot, err := loaded.LoadSnapshot(summary.ID)
	if err != nil {
		t.Fatalf("load snapshot failed: %v", err)
	}
	if snapshot.State.Services["JSProtocol"].Enabled {
		t.Fatalf("expected JSProtocol disabled in stored snapshot")
	}
}
