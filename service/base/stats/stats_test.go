package stats

import "testing"

func TestManagerTracksOperationAndServiceOperationStats(t *testing.T) {
	manager := New()

	manager.Begin("JSProtocol", "protocol.query.encode")
	manager.EndSuccess("JSProtocol", "protocol.query.encode")

	manager.Begin("JSProtocol", "protocol.query.encode")
	manager.EndFailure("JSProtocol", "protocol.query.encode", "transport_error")
	manager.RecordCooldown("JSProtocol", "protocol.query.encode")

	manager.RecordResolutionFailure("protocol.template.render", "unsupported_operation")

	serviceSnapshot := manager.Snapshot("JSProtocol")
	if serviceSnapshot.SuccessCount != 1 || serviceSnapshot.FailureCount != 1 {
		t.Fatalf("unexpected service snapshot: %#v", serviceSnapshot)
	}
	if serviceSnapshot.CooldownCount != 1 {
		t.Fatalf("expected service cooldown count to be tracked, got %#v", serviceSnapshot)
	}

	opSnapshot := manager.OperationSnapshot("protocol.query.encode")
	if opSnapshot.SuccessCount != 1 || opSnapshot.FailureCount != 1 {
		t.Fatalf("unexpected operation snapshot: %#v", opSnapshot)
	}
	if opSnapshot.LastSelectedService != "JSProtocol" {
		t.Fatalf("expected last selected service to be tracked, got %#v", opSnapshot)
	}

	ops := manager.Operations()
	if len(ops) != 2 {
		t.Fatalf("expected two operation entries, got %#v", ops)
	}

	serviceOps := manager.ServiceOperations()
	if len(serviceOps) != 1 {
		t.Fatalf("expected one service-operation entry, got %#v", serviceOps)
	}
	if serviceOps[0].CooldownCount != 1 {
		t.Fatalf("expected service-operation cooldown count to be tracked, got %#v", serviceOps[0])
	}
}
