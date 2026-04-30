package routing

import (
	"testing"
	"time"

	"easy_protocol/api"
	"easy_protocol/config"
	"easy_protocol/cooling"
	"easy_protocol/registry"
	"easy_protocol/stats"
	"easy_protocol/strategy"
)

func TestResolverPrefersConfiguredServiceForOperation(t *testing.T) {
	reg := registry.New()
	reg.Register(registry.NewService("GolangProtocol", "go", "http://127.0.0.1:11001", true, []string{"protocol.hash.sha256"}))
	reg.Register(registry.NewService("RustProtocol", "rust", "http://127.0.0.1:11004", true, []string{"protocol.hash.sha256"}))

	resolver := New(
		reg,
		cooling.New(3, time.Hour),
		strategy.New(config.SelectorSequential),
		stats.New(),
		map[string][]string{
			"protocol.hash.sha256": {"RustProtocol", "GolangProtocol"},
		},
	)

	decision, err := resolver.Resolve(api.Request{Operation: "protocol.hash.sha256"})
	if err != nil {
		t.Fatalf("resolve failed: %v", err)
	}
	if decision.SelectedService != "RustProtocol" {
		t.Fatalf("expected RustProtocol, got %s", decision.SelectedService)
	}
	if decision.Reason != "strategy_selector_preferred_service" {
		t.Fatalf("expected preferred service reason, got %s", decision.Reason)
	}
}

func TestResolverRoutingHintPreferredLanguageOverridesConfiguredService(t *testing.T) {
	reg := registry.New()
	reg.Register(registry.NewService("GolangProtocol", "go", "http://127.0.0.1:11001", true, []string{"protocol.hash.sha256"}))
	reg.Register(registry.NewService("RustProtocol", "rust", "http://127.0.0.1:11004", true, []string{"protocol.hash.sha256"}))

	resolver := New(
		reg,
		cooling.New(3, time.Hour),
		strategy.New(config.SelectorSequential),
		stats.New(),
		map[string][]string{
			"protocol.hash.sha256": {"RustProtocol", "GolangProtocol"},
		},
	)

	decision, err := resolver.Resolve(api.Request{
		Operation: "protocol.hash.sha256",
		RoutingHints: map[string]string{
			"preferred_language": "go",
		},
	})
	if err != nil {
		t.Fatalf("resolve failed: %v", err)
	}
	if decision.SelectedService != "GolangProtocol" {
		t.Fatalf("expected GolangProtocol, got %s", decision.SelectedService)
	}
	if decision.Reason != "strategy_selector_preferred_language" {
		t.Fatalf("expected preferred language reason, got %s", decision.Reason)
	}
}

func TestResolverPreviewContainsFallbackChainAndCandidateFlags(t *testing.T) {
	reg := registry.New()
	reg.Register(registry.NewService("GolangProtocol", "go", "http://127.0.0.1:11001", true, []string{"protocol.query.encode"}))
	reg.Register(registry.NewService("JSProtocol", "javascript", "http://127.0.0.1:11002", true, []string{"protocol.query.encode"}))
	reg.Register(registry.NewService("PythonProtocol", "python", "http://127.0.0.1:11003", true, []string{"protocol.query.encode"}))

	resolver := New(
		reg,
		cooling.New(3, time.Hour),
		strategy.New(config.SelectorSequential),
		stats.New(),
		map[string][]string{
			"protocol.query.encode": {"JSProtocol", "GolangProtocol"},
		},
	)

	preview := resolver.Preview(api.Request{Operation: "protocol.query.encode"})
	if preview.Error != nil {
		t.Fatalf("expected preview to succeed, got %#v", preview.Error)
	}
	if len(preview.FallbackChain) != 3 {
		t.Fatalf("expected 3 fallback candidates, got %#v", preview.FallbackChain)
	}
	if preview.FallbackChain[0] != "JSProtocol" {
		t.Fatalf("expected JSProtocol to lead fallback chain, got %#v", preview.FallbackChain)
	}
	if preview.SelectedService != "JSProtocol" {
		t.Fatalf("expected selected service to match preferred head, got %s", preview.SelectedService)
	}

	selectedCount := 0
	for _, candidate := range preview.Candidates {
		if candidate.Selected {
			selectedCount++
		}
	}
	if selectedCount != 1 {
		t.Fatalf("expected exactly one selected candidate, got %d", selectedCount)
	}
}

func TestResolverPreviewReturnsResolutionErrorWithCandidateContext(t *testing.T) {
	reg := registry.New()
	service := registry.NewService("RustProtocol", "rust", "http://127.0.0.1:11004", true, []string{"protocol.hash.sha256"})
	reg.Register(service)

	resolver := New(
		reg,
		cooling.New(3, time.Hour),
		strategy.New(config.SelectorSequential),
		stats.New(),
		nil,
	)

	preview := resolver.Preview(api.Request{
		Mode:             config.ModeSpecified,
		RequestedService: "RustProtocol",
		Operation:        "protocol.template.render",
	})
	if preview.Error == nil {
		t.Fatalf("expected preview error")
	}
	if preview.Error.Category != "unsupported_operation" {
		t.Fatalf("expected unsupported_operation, got %#v", preview.Error)
	}
	if len(preview.FallbackChain) != 1 || preview.FallbackChain[0] != "RustProtocol" {
		t.Fatalf("expected fallback chain to point at requested service, got %#v", preview.FallbackChain)
	}
}
