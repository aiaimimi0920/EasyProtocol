package stats

import "testing"

func TestManagerResetServiceAndResetAll(t *testing.T) {
	manager := New()
	manager.Begin("JSProtocol", "protocol.query.encode")
	manager.EndSuccess("JSProtocol", "protocol.query.encode")
	manager.Begin("PythonProtocol", "protocol.data.flatten")
	manager.EndFailure("PythonProtocol", "protocol.data.flatten", "validation_error")

	manager.ResetService("JSProtocol")
	services := manager.All()
	if len(services) != 1 || services[0].Service != "PythonProtocol" {
		t.Fatalf("expected only PythonProtocol stats to remain, got %#v", services)
	}

	manager.Reset()
	if len(manager.All()) != 0 {
		t.Fatalf("expected all stats to be cleared")
	}
}
