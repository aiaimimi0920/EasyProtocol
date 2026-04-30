package services

import (
	"sort"
	"strings"
	"sync"
	"time"

	"easy_protocol/api"
	"easy_protocol/config"
	"easy_protocol/routing"
)

type AttemptTrace struct {
	Attempt             int       `json:"attempt"`
	Service             string    `json:"service"`
	Outcome             string    `json:"outcome"`
	Retryable           bool      `json:"retryable"`
	CountsTowardCooling bool      `json:"counts_toward_cooling"`
	CooldownApplied     bool      `json:"cooldown_applied"`
	ErrorCategory       string    `json:"error_category,omitempty"`
	ErrorMessage        string    `json:"error_message,omitempty"`
	StartedAt           time.Time `json:"started_at"`
	FinishedAt          time.Time `json:"finished_at"`
}

type RouteTrace struct {
	TraceID             string               `json:"trace_id"`
	RequestID           string               `json:"request_id"`
	Operation           string               `json:"operation"`
	RequestMode         config.ExecutionMode `json:"request_mode"`
	StrategyMode        config.SelectorMode  `json:"strategy_mode,omitempty"`
	RequestedService    string               `json:"requested_service,omitempty"`
	PreferredLanguage   string               `json:"preferred_language,omitempty"`
	InitialReason       string               `json:"initial_reason,omitempty"`
	FinalReason         string               `json:"final_reason,omitempty"`
	FallbackChain       []string             `json:"fallback_chain,omitempty"`
	FallbackEnabled     bool                 `json:"fallback_enabled"`
	MaxFallbackAttempts int                  `json:"max_fallback_attempts"`
	RetryableCategories []string             `json:"retryable_categories,omitempty"`
	Attempts            []AttemptTrace       `json:"attempts,omitempty"`
	Preview             routing.Preview      `json:"preview"`
	FinalStatus         api.ResponseStatus   `json:"final_status"`
	FinalService        string               `json:"final_service,omitempty"`
	FinalErrorCategory  string               `json:"final_error_category,omitempty"`
	Retried             bool                 `json:"retried"`
	CreatedAt           time.Time            `json:"created_at"`
	CompletedAt         time.Time            `json:"completed_at"`
}

type TraceStore struct {
	mu      sync.Mutex
	enabled bool
	limit   int
	traces  []RouteTrace
}

type TraceFilter struct {
	Operation    string
	FinalService string
	FinalStatus  string
}

type TraceSummary struct {
	TotalTraces          int                     `json:"total_traces"`
	SuccessCount         int                     `json:"success_count"`
	FailureCount         int                     `json:"failure_count"`
	RetriedCount         int                     `json:"retried_count"`
	FallbackSuccessCount int                     `json:"fallback_success_count"`
	FallbackFailureCount int                     `json:"fallback_failure_count"`
	ByOperation          []OperationTraceSummary `json:"by_operation,omitempty"`
	ByFinalService       []ServiceTraceSummary   `json:"by_final_service,omitempty"`
}

type OperationTraceSummary struct {
	Operation            string `json:"operation"`
	TotalCount           int    `json:"total_count"`
	SuccessCount         int    `json:"success_count"`
	FailureCount         int    `json:"failure_count"`
	RetriedCount         int    `json:"retried_count"`
	FallbackSuccessCount int    `json:"fallback_success_count"`
	FallbackFailureCount int    `json:"fallback_failure_count"`
}

type ServiceTraceSummary struct {
	Service               string `json:"service"`
	FinalSuccessCount     int    `json:"final_success_count"`
	FinalFailureCount     int    `json:"final_failure_count"`
	AttemptCount          int    `json:"attempt_count"`
	RetryableFailureCount int    `json:"retryable_failure_count"`
	CooldownAppliedCount  int    `json:"cooldown_applied_count"`
}

func NewTraceStore(enabled bool, limit int) *TraceStore {
	if limit <= 0 {
		limit = 200
	}
	return &TraceStore{
		enabled: enabled,
		limit:   limit,
	}
}

func (s *TraceStore) Enabled() bool {
	if s == nil {
		return false
	}
	return s.enabled
}

func (s *TraceStore) Record(trace RouteTrace) {
	if s == nil || !s.enabled {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	trace.FallbackChain = append([]string(nil), trace.FallbackChain...)
	trace.Attempts = append([]AttemptTrace(nil), trace.Attempts...)
	trace.RetryableCategories = append([]string(nil), trace.RetryableCategories...)
	s.traces = append([]RouteTrace{trace}, s.traces...)
	if len(s.traces) > s.limit {
		s.traces = s.traces[:s.limit]
	}
}

func (s *TraceStore) List() []RouteTrace {
	if s == nil {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]RouteTrace, len(s.traces))
	copy(out, s.traces)
	return out
}

func (s *TraceStore) FindByRequestID(requestID string) []RouteTrace {
	if s == nil {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]RouteTrace, 0)
	for _, trace := range s.traces {
		if trace.RequestID == requestID {
			out = append(out, trace)
		}
	}
	return out
}

func (s *TraceStore) Clear() {
	if s == nil {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.traces = nil
}

func (s *TraceStore) Summary(filter TraceFilter) TraceSummary {
	if s == nil {
		return TraceSummary{}
	}
	s.mu.Lock()
	defer s.mu.Unlock()

	operationTotals := make(map[string]*OperationTraceSummary)
	serviceTotals := make(map[string]*ServiceTraceSummary)
	summary := TraceSummary{}

	for _, trace := range s.traces {
		if !matchesTraceFilter(trace, filter) {
			continue
		}
		summary.TotalTraces++
		if trace.FinalStatus == api.StatusSucceeded {
			summary.SuccessCount++
		} else {
			summary.FailureCount++
		}
		if trace.Retried {
			summary.RetriedCount++
			if trace.FinalStatus == api.StatusSucceeded {
				summary.FallbackSuccessCount++
			} else {
				summary.FallbackFailureCount++
			}
		}

		opEntry := ensureOperationSummary(operationTotals, trace.Operation)
		opEntry.TotalCount++
		if trace.FinalStatus == api.StatusSucceeded {
			opEntry.SuccessCount++
		} else {
			opEntry.FailureCount++
		}
		if trace.Retried {
			opEntry.RetriedCount++
			if trace.FinalStatus == api.StatusSucceeded {
				opEntry.FallbackSuccessCount++
			} else {
				opEntry.FallbackFailureCount++
			}
		}

		if strings.TrimSpace(trace.FinalService) != "" {
			serviceEntry := ensureServiceSummary(serviceTotals, trace.FinalService)
			if trace.FinalStatus == api.StatusSucceeded {
				serviceEntry.FinalSuccessCount++
			} else {
				serviceEntry.FinalFailureCount++
			}
			serviceEntry.AttemptCount += len(trace.Attempts)
			for _, attempt := range trace.Attempts {
				if attempt.Retryable && attempt.Outcome == "failed" {
					serviceEntry.RetryableFailureCount++
				}
				if attempt.CooldownApplied {
					serviceEntry.CooldownAppliedCount++
				}
			}
		}
	}

	summary.ByOperation = flattenOperationSummaries(operationTotals)
	summary.ByFinalService = flattenServiceSummaries(serviceTotals)
	return summary
}

func matchesTraceFilter(trace RouteTrace, filter TraceFilter) bool {
	if item := strings.TrimSpace(filter.Operation); item != "" && trace.Operation != item {
		return false
	}
	if item := strings.TrimSpace(filter.FinalService); item != "" && trace.FinalService != item {
		return false
	}
	if item := strings.TrimSpace(filter.FinalStatus); item != "" && string(trace.FinalStatus) != item {
		return false
	}
	return true
}

func ensureOperationSummary(entries map[string]*OperationTraceSummary, operation string) *OperationTraceSummary {
	entry, ok := entries[operation]
	if ok {
		return entry
	}
	entry = &OperationTraceSummary{Operation: operation}
	entries[operation] = entry
	return entry
}

func ensureServiceSummary(entries map[string]*ServiceTraceSummary, service string) *ServiceTraceSummary {
	entry, ok := entries[service]
	if ok {
		return entry
	}
	entry = &ServiceTraceSummary{Service: service}
	entries[service] = entry
	return entry
}

func flattenOperationSummaries(entries map[string]*OperationTraceSummary) []OperationTraceSummary {
	out := make([]OperationTraceSummary, 0, len(entries))
	for _, entry := range entries {
		out = append(out, *entry)
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].Operation < out[j].Operation
	})
	return out
}

func flattenServiceSummaries(entries map[string]*ServiceTraceSummary) []ServiceTraceSummary {
	out := make([]ServiceTraceSummary, 0, len(entries))
	for _, entry := range entries {
		out = append(out, *entry)
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].Service < out[j].Service
	})
	return out
}
