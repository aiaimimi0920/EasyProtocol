package services

import (
	"context"
	"testing"
	"time"

	"easy_protocol/api"
	"easy_protocol/attribution"
	"easy_protocol/config"
	"easy_protocol/cooling"
	"easy_protocol/registry"
	"easy_protocol/stats"
	"easy_protocol/transports"
)

func TestPolicyReportIncludesKnownOperations(t *testing.T) {
	cfg := config.DefaultConfig()
	reg := registry.New()
	reg.Register(registry.NewService("GolangProtocol", "go", "http://127.0.0.1:11001", true, []string{"protocol.query.encode"}))
	reg.Register(registry.NewService("JSProtocol", "javascript", "http://127.0.0.1:11002", true, []string{"protocol.template.render"}))

	dispatcher := NewDispatcher(
		cfg,
		reg,
		nil,
		cooling.New(3, time.Hour),
		attribution.NewManager(20),
		stats.New(),
		NewTraceStore(false, 20),
		transports.StubTransport{},
	)

	report, err := dispatcher.PolicyReport(context.Background())
	if err != nil {
		t.Fatalf("policy report failed: %v", err)
	}
	if report.Global.MaxFallbackAttempts != cfg.Strategy.MaxFallbackAttempts {
		t.Fatalf("unexpected global policy snapshot: %#v", report.Global)
	}
	foundQuery := false
	foundTemplate := false
	for _, item := range report.Operations {
		if item.Operation == "protocol.query.encode" {
			foundQuery = true
		}
		if item.Operation == "protocol.template.render" {
			foundTemplate = true
		}
	}
	if !foundQuery || !foundTemplate {
		t.Fatalf("expected known operations in report, got %#v", report.Operations)
	}
}

func TestSimulateRouteAppliesOverridePreferredServicesAndPolicy(t *testing.T) {
	cfg := config.DefaultConfig()
	reg := registry.New()
	reg.Register(registry.NewService("GolangProtocol", "go", "http://127.0.0.1:11001", true, []string{"protocol.query.encode"}))
	reg.Register(registry.NewService("JSProtocol", "javascript", "http://127.0.0.1:11002", true, []string{"protocol.query.encode"}))

	dispatcher := NewDispatcher(
		cfg,
		reg,
		nil,
		cooling.New(3, time.Hour),
		attribution.NewManager(20),
		stats.New(),
		NewTraceStore(false, 20),
		transports.StubTransport{},
	)

	result, err := dispatcher.SimulateRoute(context.Background(), RouteSimulationRequest{
		Request: api.Request{
			Operation: "protocol.query.encode",
		},
		Override: RouteSimulationOverride{
			PreferredServices:   []string{"GolangProtocol", "JSProtocol"},
			FallbackMode:        "disabled",
			MaxFallbackAttempts: 1,
		},
	})
	if err != nil {
		t.Fatalf("simulate route failed: %v", err)
	}
	if result.BaselinePreview.SelectedService != "JSProtocol" {
		t.Fatalf("expected baseline to prefer JSProtocol, got %#v", result.BaselinePreview)
	}
	if result.SimulatedPreview.SelectedService != "GolangProtocol" {
		t.Fatalf("expected simulated preview to prefer GolangProtocol, got %#v", result.SimulatedPreview)
	}
	if !result.Differences["selected_service_changed"] {
		t.Fatalf("expected selected_service_changed difference, got %#v", result.Differences)
	}
	if result.SimulatedPolicy.FallbackEnabled {
		t.Fatalf("expected simulated policy fallback to be disabled, got %#v", result.SimulatedPolicy)
	}
	if result.SimulatedPreview.MaxFallbackAttempts != 1 {
		t.Fatalf("expected simulated preview max attempts to be 1, got %#v", result.SimulatedPreview)
	}
}

func TestUpdatePoliciesChangesEffectivePolicyAndPreferredServices(t *testing.T) {
	cfg := config.DefaultConfig()
	reg := registry.New()
	reg.Register(registry.NewService("GolangProtocol", "go", "http://127.0.0.1:11001", true, []string{"protocol.query.encode"}))
	reg.Register(registry.NewService("JSProtocol", "javascript", "http://127.0.0.1:11002", true, []string{"protocol.query.encode"}))

	dispatcher := NewDispatcher(
		cfg,
		reg,
		nil,
		cooling.New(3, time.Hour),
		attribution.NewManager(20),
		stats.New(),
		NewTraceStore(false, 20),
		transports.StubTransport{},
	)

	disable := "disabled"
	maxAttempts := 1
	report, err := dispatcher.UpdatePolicies(PolicyUpdateRequest{
		Global: &GlobalPolicyPatch{
			MaxFallbackAttempts: &maxAttempts,
		},
		Operations: []OperationPolicyUpdate{
			{
				Operation:         "protocol.query.encode",
				PreferredServices: []string{"GolangProtocol", "JSProtocol"},
				FallbackMode:      &disable,
			},
		},
	})
	if err != nil {
		t.Fatalf("update policies failed: %v", err)
	}
	if report.Global.MaxFallbackAttempts != 1 {
		t.Fatalf("expected global max fallback attempts to be updated, got %#v", report.Global)
	}

	snapshot := dispatcher.PolicySnapshot("protocol.query.encode")
	if snapshot.PreferredServices[0] != "GolangProtocol" {
		t.Fatalf("expected preferred services update to apply, got %#v", snapshot)
	}
	if snapshot.FallbackEnabled {
		t.Fatalf("expected fallback to be disabled by operation policy, got %#v", snapshot)
	}

	preview, err := dispatcher.PreviewRoute(context.Background(), api.Request{Operation: "protocol.query.encode"})
	if err != nil {
		t.Fatalf("preview route failed: %v", err)
	}
	if preview.SelectedService != "GolangProtocol" {
		t.Fatalf("expected updated preference to affect preview, got %#v", preview)
	}
	if preview.FallbackEnabled {
		t.Fatalf("expected preview fallback to be disabled after update, got %#v", preview)
	}
}
