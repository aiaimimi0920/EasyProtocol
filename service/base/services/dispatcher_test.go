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

type failingTransport struct {
	err error
}

func (f failingTransport) Call(_ context.Context, _ string, _ api.Request) (transports.Result, error) {
	return transports.Result{}, f.err
}

type scriptedTransport struct {
	outcomes map[string][]scriptedOutcome
}

type scriptedOutcome struct {
	result transports.Result
	err    error
}

func (s *scriptedTransport) Call(_ context.Context, service string, _ api.Request) (transports.Result, error) {
	queue := s.outcomes[service]
	if len(queue) == 0 {
		return transports.Result{}, &transports.ServiceCallError{
			Category: attribution.CategoryTransportError,
			Message:  "unexpected service call",
		}
	}
	outcome := queue[0]
	s.outcomes[service] = queue[1:]
	return outcome.result, outcome.err
}

func TestDispatcherStrategyModeReturnsSuccess(t *testing.T) {
	cfg := config.DefaultConfig()
	reg := registry.New()
	for _, service := range cfg.Services {
		reg.Register(registry.NewService(service.Name, service.Language, service.Endpoint, service.Enabled, nil))
	}

	dispatcher := NewDispatcher(
		cfg,
		reg,
		nil,
		cooling.New(cfg.Strategy.FailureThreshold, cfg.Strategy.CooldownDuration),
		attribution.NewManager(20),
		stats.New(),
		NewTraceStore(false, 20),
		transports.StubTransport{},
	)

	resp, err := dispatcher.Dispatch(context.Background(), api.Request{Operation: "health.inspect"})
	if err != nil {
		t.Fatalf("dispatch failed: %v", err)
	}
	if resp.Status != api.StatusSucceeded {
		t.Fatalf("expected success response, got %s", resp.Status)
	}
	if resp.SelectedService == "" {
		t.Fatalf("expected selected service to be set")
	}
}

func TestDispatcherSpecifiedModeRespectsCooling(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Mode = config.ModeSpecified

	reg := registry.New()
	reg.Register(registry.NewService("JSProtocol", "javascript", "http://127.0.0.1:11002", true, nil))

	coolingMgr := cooling.New(1, time.Hour)
	coolingMgr.RecordFailure("JSProtocol", attribution.CategoryTransportError, true, time.Now().UTC())

	dispatcher := NewDispatcher(cfg, reg, nil, coolingMgr, attribution.NewManager(20), stats.New(), NewTraceStore(false, 20), transports.StubTransport{})
	resp, err := dispatcher.Dispatch(context.Background(), api.Request{
		Mode:             config.ModeSpecified,
		Operation:        "health.inspect",
		RequestedService: "JSProtocol",
	})
	if err != nil {
		t.Fatalf("dispatch failed: %v", err)
	}
	if resp.Status != api.StatusFailed {
		t.Fatalf("expected failed response, got %s", resp.Status)
	}
	if resp.Error == nil || resp.Error.Category != attribution.CategoryServiceCooled {
		t.Fatalf("expected service_cooled attribution, got %#v", resp.Error)
	}
}

func TestDispatcherRecordsTransportFailure(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Mode = config.ModeSpecified

	reg := registry.New()
	reg.Register(registry.NewService("GolangProtocol", "go", "http://127.0.0.1:11001", true, nil))

	dispatcher := NewDispatcher(
		cfg,
		reg,
		nil,
		cooling.New(1, time.Hour),
		attribution.NewManager(20),
		stats.New(),
		NewTraceStore(false, 20),
		failingTransport{err: &transports.ServiceCallError{
			Category: attribution.CategoryTransportError,
			Message:  "dial tcp failed",
		}},
	)

	resp, err := dispatcher.Dispatch(context.Background(), api.Request{
		Mode:             config.ModeSpecified,
		Operation:        "health.inspect",
		RequestedService: "GolangProtocol",
	})
	if err != nil {
		t.Fatalf("dispatch failed: %v", err)
	}
	if resp.Status != api.StatusFailed {
		t.Fatalf("expected failed response, got %s", resp.Status)
	}
	if resp.Error == nil || resp.Error.Category != attribution.CategoryTransportError {
		t.Fatalf("expected transport_error attribution, got %#v", resp.Error)
	}
	if !resp.Meta.CooldownApplied {
		t.Fatalf("expected cooldown to be applied after threshold-1 transport failure")
	}
}

func TestDispatcherRetriesFallbackChainOnRetryableError(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Mode = config.ModeStrategy
	cfg.Strategy.MaxFallbackAttempts = 3
	cfg.Strategy.FallbackOnRetryableErrors = true

	reg := registry.New()
	reg.Register(registry.NewService("GolangProtocol", "go", "http://127.0.0.1:11001", true, []string{"protocol.query.encode"}))
	reg.Register(registry.NewService("JSProtocol", "javascript", "http://127.0.0.1:11002", true, []string{"protocol.query.encode"}))

	traceStore := NewTraceStore(true, 20)
	statsMgr := stats.New()
	dispatcher := NewDispatcher(
		cfg,
		reg,
		nil,
		cooling.New(2, time.Hour),
		attribution.NewManager(20),
		statsMgr,
		traceStore,
		&scriptedTransport{
			outcomes: map[string][]scriptedOutcome{
				"JSProtocol": {{
					err: &transports.ServiceCallError{
						Category: attribution.CategoryTransportError,
						Message:  "temporary connect failure",
					},
				}},
				"GolangProtocol": {{
					result: transports.Result{
						Payload: map[string]any{"language": "go"},
					},
				}},
			},
		},
	)

	resp, err := dispatcher.Dispatch(context.Background(), api.Request{Operation: "protocol.query.encode"})
	if err != nil {
		t.Fatalf("dispatch failed: %v", err)
	}
	if resp.Status != api.StatusSucceeded {
		t.Fatalf("expected success after fallback, got %s", resp.Status)
	}
	if resp.SelectedService != "GolangProtocol" {
		t.Fatalf("expected fallback to GolangProtocol, got %s", resp.SelectedService)
	}
	if !resp.Meta.Retried || resp.Meta.AttemptCount != 2 {
		t.Fatalf("expected retry metadata to be populated, got %#v", resp.Meta)
	}
	if resp.Meta.RouteReason != "fallback_retry_success" {
		t.Fatalf("expected fallback_retry_success reason, got %s", resp.Meta.RouteReason)
	}
	if len(resp.Meta.FallbackChain) < 2 || resp.Meta.FallbackChain[0] != "JSProtocol" || resp.Meta.FallbackChain[1] != "GolangProtocol" {
		t.Fatalf("unexpected fallback chain: %#v", resp.Meta.FallbackChain)
	}

	traces := traceStore.FindByRequestID(resp.RequestID)
	if len(traces) != 1 {
		t.Fatalf("expected one trace, got %#v", traces)
	}
	if len(traces[0].Attempts) != 2 {
		t.Fatalf("expected two attempts in trace, got %#v", traces[0].Attempts)
	}
	if traces[0].Attempts[0].Service != "JSProtocol" || traces[0].Attempts[1].Service != "GolangProtocol" {
		t.Fatalf("unexpected attempt order: %#v", traces[0].Attempts)
	}
}

func TestDispatcherDoesNotFallbackInSpecifiedMode(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Mode = config.ModeSpecified
	cfg.Strategy.FallbackOnRetryableErrors = true

	reg := registry.New()
	reg.Register(registry.NewService("GolangProtocol", "go", "http://127.0.0.1:11001", true, []string{"protocol.query.encode"}))
	reg.Register(registry.NewService("JSProtocol", "javascript", "http://127.0.0.1:11002", true, []string{"protocol.query.encode"}))

	traceStore := NewTraceStore(true, 20)
	dispatcher := NewDispatcher(
		cfg,
		reg,
		nil,
		cooling.New(3, time.Hour),
		attribution.NewManager(20),
		stats.New(),
		traceStore,
		&scriptedTransport{
			outcomes: map[string][]scriptedOutcome{
				"JSProtocol": {{
					err: &transports.ServiceCallError{
						Category: attribution.CategoryTransportError,
						Message:  "temporary connect failure",
					},
				}},
			},
		},
	)

	resp, err := dispatcher.Dispatch(context.Background(), api.Request{
		Mode:             config.ModeSpecified,
		Operation:        "protocol.query.encode",
		RequestedService: "JSProtocol",
	})
	if err != nil {
		t.Fatalf("dispatch failed: %v", err)
	}
	if resp.Status != api.StatusFailed {
		t.Fatalf("expected failure response, got %s", resp.Status)
	}
	if resp.Meta.Retried || resp.Meta.AttemptCount != 1 {
		t.Fatalf("expected no retry in specified mode, got %#v", resp.Meta)
	}

	traces := traceStore.FindByRequestID(resp.RequestID)
	if len(traces) != 1 || len(traces[0].Attempts) != 1 {
		t.Fatalf("expected single-attempt trace, got %#v", traces)
	}
}

func TestDispatcherOperationPolicyCanDisableFallback(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Mode = config.ModeStrategy
	cfg.Strategy.FallbackOnRetryableErrors = true
	cfg.Strategy.OperationPolicies["protocol.query.encode"] = config.OperationPolicyConfig{
		FallbackMode: "disabled",
	}

	reg := registry.New()
	reg.Register(registry.NewService("GolangProtocol", "go", "http://127.0.0.1:11001", true, []string{"protocol.query.encode"}))
	reg.Register(registry.NewService("JSProtocol", "javascript", "http://127.0.0.1:11002", true, []string{"protocol.query.encode"}))

	traceStore := NewTraceStore(true, 20)
	dispatcher := NewDispatcher(
		cfg,
		reg,
		nil,
		cooling.New(3, time.Hour),
		attribution.NewManager(20),
		stats.New(),
		traceStore,
		&scriptedTransport{
			outcomes: map[string][]scriptedOutcome{
				"JSProtocol": {{
					err: &transports.ServiceCallError{
						Category: attribution.CategoryTransportError,
						Message:  "temporary connect failure",
					},
				}},
			},
		},
	)

	resp, err := dispatcher.Dispatch(context.Background(), api.Request{Operation: "protocol.query.encode"})
	if err != nil {
		t.Fatalf("dispatch failed: %v", err)
	}
	if resp.Status != api.StatusFailed {
		t.Fatalf("expected failure without fallback, got %s", resp.Status)
	}
	if resp.Meta.Retried || resp.Meta.AttemptCount != 1 {
		t.Fatalf("expected no retry when operation policy disables fallback, got %#v", resp.Meta)
	}
}

func TestDispatcherOperationPolicyCanOverrideRetryableCategories(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Mode = config.ModeStrategy
	cfg.Strategy.FallbackOnRetryableErrors = true
	cfg.Strategy.OperationPolicies["protocol.query.encode"] = config.OperationPolicyConfig{
		FallbackMode:        "enabled",
		MaxFallbackAttempts: 2,
		RetryableCategories: []string{attribution.CategoryServiceUnavailable},
	}

	reg := registry.New()
	reg.Register(registry.NewService("GolangProtocol", "go", "http://127.0.0.1:11001", true, []string{"protocol.query.encode"}))
	reg.Register(registry.NewService("JSProtocol", "javascript", "http://127.0.0.1:11002", true, []string{"protocol.query.encode"}))

	traceStore := NewTraceStore(true, 20)
	dispatcher := NewDispatcher(
		cfg,
		reg,
		nil,
		cooling.New(3, time.Hour),
		attribution.NewManager(20),
		stats.New(),
		traceStore,
		&scriptedTransport{
			outcomes: map[string][]scriptedOutcome{
				"JSProtocol": {{
					err: &transports.ServiceCallError{
						Category: attribution.CategoryTransportError,
						Message:  "temporary connect failure",
					},
				}},
			},
		},
	)

	resp, err := dispatcher.Dispatch(context.Background(), api.Request{Operation: "protocol.query.encode"})
	if err != nil {
		t.Fatalf("dispatch failed: %v", err)
	}
	if resp.Status != api.StatusFailed {
		t.Fatalf("expected failure without fallback, got %s", resp.Status)
	}
	if resp.Meta.Retried || resp.Meta.AttemptCount != 1 {
		t.Fatalf("expected retry suppression from override categories, got %#v", resp.Meta)
	}
}
