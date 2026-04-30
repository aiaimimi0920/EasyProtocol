package services

import (
	"path/filepath"
	"testing"
)

func TestAuditStoreRecordLoadAndFilter(t *testing.T) {
	tempDir := t.TempDir()
	path := filepath.Join(tempDir, "audit-log.jsonl")

	store := NewAuditStore(true, path, 20)
	if err := store.Record(AuditRecord{Action: "policy_update", TargetType: "policy", Target: "protocol.query.encode"}); err != nil {
		t.Fatalf("record failed: %v", err)
	}
	if err := store.Record(AuditRecord{Action: "service_action", TargetType: "service", Target: "JSProtocol"}); err != nil {
		t.Fatalf("record failed: %v", err)
	}

	loaded := NewAuditStore(true, path, 20)
	if err := loaded.Load(); err != nil {
		t.Fatalf("load failed: %v", err)
	}

	filtered := loaded.List(AuditFilter{TargetType: "service"})
	if len(filtered) != 1 {
		t.Fatalf("expected one service audit record, got %#v", filtered)
	}
	if filtered[0].Target != "JSProtocol" {
		t.Fatalf("unexpected audit record target: %#v", filtered[0])
	}
}

func TestAuditStorePruneAndRetentionSummary(t *testing.T) {
	tempDir := t.TempDir()
	path := filepath.Join(tempDir, "audit-log.jsonl")

	store := NewAuditStore(true, path, 20)
	if err := store.Record(AuditRecord{Action: "policy_update", TargetType: "policy", Target: "protocol.query.encode"}); err != nil {
		t.Fatalf("record failed: %v", err)
	}
	if err := store.Record(AuditRecord{Action: "service_action", TargetType: "service", Target: "JSProtocol"}); err != nil {
		t.Fatalf("record failed: %v", err)
	}
	if err := store.Record(AuditRecord{Action: "control_plane_freeze", TargetType: "control_plane", Target: "freeze"}); err != nil {
		t.Fatalf("record failed: %v", err)
	}

	before := store.RetentionSummary()
	if before.RecordCount != 3 {
		t.Fatalf("expected three audit records before prune, got %#v", before)
	}

	pruned, err := store.Prune(2)
	if err != nil {
		t.Fatalf("prune failed: %v", err)
	}
	if pruned.BeforeCount != 3 || pruned.AfterCount != 2 || pruned.PrunedCount != 1 {
		t.Fatalf("unexpected prune summary: %#v", pruned)
	}

	after := store.RetentionSummary()
	if after.RecordCount != 2 {
		t.Fatalf("expected two audit records after prune, got %#v", after)
	}
	if !after.FileExists || after.FileSizeBytes == 0 {
		t.Fatalf("expected retained audit file after prune, got %#v", after)
	}

	reloaded := NewAuditStore(true, path, 20)
	if err := reloaded.Load(); err != nil {
		t.Fatalf("reload after prune failed: %v", err)
	}
	items := reloaded.List(AuditFilter{})
	if len(items) != 2 {
		t.Fatalf("expected two records after reload, got %#v", items)
	}
}
