package services

import (
	"testing"
	"time"

	"easy_protocol/api"
)

func TestTraceStoreSummaryAggregatesAndFilters(t *testing.T) {
	store := NewTraceStore(true, 20)

	store.Record(RouteTrace{
		TraceID:      "trace-1",
		RequestID:    "req-1",
		Operation:    "protocol.query.encode",
		FinalStatus:  api.StatusSucceeded,
		FinalService: "GolangProtocol",
		Retried:      true,
		Attempts: []AttemptTrace{
			{Attempt: 1, Service: "JSProtocol", Outcome: "failed", Retryable: true},
			{Attempt: 2, Service: "GolangProtocol", Outcome: "succeeded"},
		},
		CreatedAt:   time.Now().UTC(),
		CompletedAt: time.Now().UTC(),
	})

	store.Record(RouteTrace{
		TraceID:      "trace-2",
		RequestID:    "req-2",
		Operation:    "protocol.regex.extract",
		FinalStatus:  api.StatusFailed,
		FinalService: "PythonProtocol",
		Attempts: []AttemptTrace{
			{Attempt: 1, Service: "PythonProtocol", Outcome: "failed", Retryable: false, CooldownApplied: true},
		},
		CreatedAt:   time.Now().UTC(),
		CompletedAt: time.Now().UTC(),
	})

	summary := store.Summary(TraceFilter{})
	if summary.TotalTraces != 2 {
		t.Fatalf("expected 2 traces, got %#v", summary)
	}
	if summary.FallbackSuccessCount != 1 || summary.FallbackFailureCount != 0 {
		t.Fatalf("unexpected fallback counters: %#v", summary)
	}
	if len(summary.ByOperation) != 2 {
		t.Fatalf("expected per-operation summary entries, got %#v", summary.ByOperation)
	}

	filtered := store.Summary(TraceFilter{Operation: "protocol.query.encode"})
	if filtered.TotalTraces != 1 {
		t.Fatalf("expected single filtered trace, got %#v", filtered)
	}
	if len(filtered.ByFinalService) != 1 || filtered.ByFinalService[0].Service != "GolangProtocol" {
		t.Fatalf("unexpected filtered service summary: %#v", filtered.ByFinalService)
	}
}

func TestTraceStoreClearRemovesAllTraces(t *testing.T) {
	store := NewTraceStore(true, 10)
	store.Record(RouteTrace{TraceID: "trace-1", RequestID: "req-1"})
	store.Record(RouteTrace{TraceID: "trace-2", RequestID: "req-2"})

	store.Clear()

	if traces := store.List(); len(traces) != 0 {
		t.Fatalf("expected traces to be cleared, got %#v", traces)
	}
}
