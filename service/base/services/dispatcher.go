package services

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"easy_protocol/api"
	"easy_protocol/attribution"
	"easy_protocol/config"
	"easy_protocol/cooling"
	"easy_protocol/registry"
	"easy_protocol/routing"
	"easy_protocol/stats"
	"easy_protocol/strategy"
	"easy_protocol/transports"
)

type Dispatcher struct {
	mu                  sync.RWMutex
	defaultMode         config.ExecutionMode
	strategyMode        config.SelectorMode
	selector            strategy.Selector
	attributionEnabled  bool
	enableFallbackRetry bool
	maxFallbackAttempts int
	retryableCategories []string
	preferredServices   map[string][]string
	operationPolicies   map[string]config.OperationPolicyConfig
	registry            *registry.Registry
	refresher           registry.Refresher
	cooling             *cooling.Manager
	attributions        *attribution.Manager
	stats               *stats.Manager
	traceStore          *TraceStore
	transport           transports.ServiceTransport
}

type effectiveOperationPolicy struct {
	AllowFallback       bool
	MaxFallbackAttempts int
	RetryableCategories []string
}

func NewDispatcher(
	cfg config.Config,
	reg *registry.Registry,
	refresher registry.Refresher,
	coolingMgr *cooling.Manager,
	attributionMgr *attribution.Manager,
	statsMgr *stats.Manager,
	traceStore *TraceStore,
	transport transports.ServiceTransport,
) *Dispatcher {
	cfg.Normalize()
	return &Dispatcher{
		defaultMode:         cfg.Mode,
		strategyMode:        cfg.Strategy.Mode,
		selector:            strategy.New(cfg.Strategy.Mode),
		attributionEnabled:  cfg.ErrorAttribution.Enabled,
		enableFallbackRetry: cfg.Strategy.FallbackOnRetryableErrors,
		maxFallbackAttempts: cfg.Strategy.MaxFallbackAttempts,
		retryableCategories: append([]string(nil), cfg.Strategy.RetryableCategories...),
		preferredServices:   copyPreferredServices(cfg.Strategy.PreferredServicesByOperation),
		operationPolicies:   cfg.Strategy.OperationPolicies,
		registry:            reg,
		refresher:           refresher,
		cooling:             coolingMgr,
		attributions:        attributionMgr,
		stats:               statsMgr,
		traceStore:          traceStore,
		transport:           transport,
	}
}

func (d *Dispatcher) Dispatch(ctx context.Context, req api.Request) (api.Response, error) {
	normalized := d.normalizeRequest(req)
	if d.refresher != nil {
		_ = d.refresher.RefreshAll(ctx)
	}

	preview := d.resolverForCurrentPolicy().Preview(normalized)
	policy := d.policyFor(normalized.Operation)
	d.applyPolicyToPreview(&preview, policy)
	trace := d.newTrace(normalized, preview, policy)
	if preview.Error != nil {
		d.stats.RecordResolutionFailure(normalized.Operation, preview.Error.Category)
		record := d.newRecord(normalized.ID, preview.Error.Service, preview.Error.Category, preview.Error.Message, map[string]any{
			"operation": normalized.Operation,
		})
		return d.failureResponse(
			normalized.ID,
			preview.Error.Service,
			normalized.Mode,
			d.strategyMode,
			"resolution_failed",
			false,
			preview.FallbackChain,
			trace,
			record,
		), nil
	}

	attemptServices := d.attemptOrder(preview, policy)
	var finalRecord *attribution.Record
	var lastService string
	var lastCooldownApplied bool

	for attemptIndex, service := range attemptServices {
		now := time.Now().UTC()
		lastService = service
		d.stats.Begin(service, normalized.Operation)
		result, callErr := d.transport.Call(ctx, service, normalized)
		if callErr == nil {
			d.cooling.RecordSuccess(service, now)
			d.stats.EndSuccess(service, normalized.Operation)
			if trace != nil {
				trace.Attempts = append(trace.Attempts, AttemptTrace{
					Attempt:    attemptIndex + 1,
					Service:    service,
					Outcome:    "succeeded",
					StartedAt:  now,
					FinishedAt: time.Now().UTC(),
				})
				trace.FinalStatus = api.StatusSucceeded
				trace.FinalService = service
				trace.Retried = attemptIndex > 0
				trace.FinalReason = finalRouteReason(preview.Reason, attemptIndex)
				trace.CompletedAt = time.Now().UTC()
				d.traceStore.Record(*trace)
			}

			return api.Response{
				RequestID:       normalized.ID,
				SelectedService: service,
				Status:          api.StatusSucceeded,
				Result:          result.Payload,
				Meta: api.ResponseMeta{
					RequestMode:     normalized.Mode,
					StrategyMode:    preview.StrategyMode,
					RouteReason:     finalRouteReason(preview.Reason, attemptIndex),
					FallbackChain:   preview.FallbackChain,
					TraceID:         traceID(trace),
					AttemptCount:    attemptIndex + 1,
					Retried:         attemptIndex > 0,
					CooldownApplied: false,
				},
			}, nil
		}

		record, cooldownApplied := d.recordTransportFailure(normalized.ID, service, normalized.Operation, callErr, now)
		lastCooldownApplied = cooldownApplied
		finalRecord = record
		retryable := d.isRetryableFailure(record, policy)
		if trace != nil {
			trace.Attempts = append(trace.Attempts, AttemptTrace{
				Attempt:             attemptIndex + 1,
				Service:             service,
				Outcome:             "failed",
				Retryable:           retryable,
				CountsTowardCooling: record != nil && record.CountsTowardCooling,
				CooldownApplied:     cooldownApplied,
				ErrorCategory:       recordCategory(record),
				ErrorMessage:        recordMessage(record),
				StartedAt:           now,
				FinishedAt:          time.Now().UTC(),
			})
		}
		if !d.shouldRetry(preview, policy, attemptIndex, retryable) {
			break
		}
	}

	return d.failureResponse(
		normalized.ID,
		lastService,
		normalized.Mode,
		preview.StrategyMode,
		finalFailureReason(preview.Reason, len(attemptServices), trace),
		lastCooldownApplied,
		preview.FallbackChain,
		trace,
		finalRecord,
	), nil
}

func (d *Dispatcher) PreviewRoute(ctx context.Context, req api.Request) (routing.Preview, error) {
	normalized := d.normalizeRequest(req)
	if d.refresher != nil {
		_ = d.refresher.RefreshAll(ctx)
	}
	preview := d.resolverForCurrentPolicy().Preview(normalized)
	policy := d.policyFor(normalized.Operation)
	d.applyPolicyToPreview(&preview, policy)
	return preview, nil
}

func (d *Dispatcher) normalizeRequest(req api.Request) api.Request {
	if req.ID == "" {
		req.ID = fmt.Sprintf("req-%d", time.Now().UnixNano())
	}
	if req.Mode == "" {
		req.Mode = d.defaultMode
	}
	if req.Payload == nil {
		req.Payload = map[string]any{}
	}
	if req.RoutingHints == nil {
		req.RoutingHints = map[string]string{}
	}
	return req
}

func (d *Dispatcher) newRecord(requestID, service, category, message string, details map[string]any) *attribution.Record {
	if !d.attributionEnabled {
		return nil
	}
	record := attribution.NewRecord(requestID, service, category, message, details)
	if d.attributions != nil {
		d.attributions.Record(record)
	}
	return &record
}

func (d *Dispatcher) recordTransportFailure(requestID, service, operation string, err error, now time.Time) (*attribution.Record, bool) {
	category := attribution.CategoryDelegationError
	details := map[string]any{}
	message := err.Error()

	var resolutionErr *routing.ResolutionError
	if errors.As(err, &resolutionErr) {
		if resolutionErr.Category != "" {
			category = resolutionErr.Category
		}
		if resolutionErr.Message != "" {
			message = resolutionErr.Message
		}
	} else {
		var callErr *transports.ServiceCallError
		if errors.As(err, &callErr) {
			if callErr.Category != "" {
				category = callErr.Category
			}
			if callErr.Details != nil {
				details = callErr.Details
			}
		}
	}

	record := d.newRecord(requestID, service, category, message, details)
	countsTowardCooling := record != nil && record.CountsTowardCooling
	state := d.cooling.RecordFailure(service, category, countsTowardCooling, now)
	d.stats.EndFailure(service, operation, category)
	if state.Cooled {
		d.stats.RecordCooldown(service, operation)
	}
	return record, record != nil && record.CountsTowardCooling && d.cooling.IsCooled(service, now)
}

func (d *Dispatcher) failureResponse(
	requestID string,
	service string,
	mode config.ExecutionMode,
	strategyMode config.SelectorMode,
	reason string,
	cooldownApplied bool,
	fallbackChain []string,
	trace *RouteTrace,
	record *attribution.Record,
) api.Response {
	if trace != nil {
		trace.FinalStatus = api.StatusFailed
		trace.FinalService = service
		trace.FinalReason = reason
		trace.FinalErrorCategory = recordCategory(record)
		trace.Retried = len(trace.Attempts) > 1
		trace.CompletedAt = time.Now().UTC()
		d.traceStore.Record(*trace)
	}
	return api.Response{
		RequestID:       requestID,
		SelectedService: service,
		Status:          api.StatusFailed,
		Error:           record,
		Meta: api.ResponseMeta{
			RequestMode:     mode,
			StrategyMode:    strategyMode,
			RouteReason:     reason,
			FallbackChain:   append([]string(nil), fallbackChain...),
			TraceID:         traceID(trace),
			AttemptCount:    attemptCount(trace),
			Retried:         wasRetried(trace),
			CooldownApplied: cooldownApplied,
		},
	}
}

func (d *Dispatcher) attemptOrder(preview routing.Preview, policy effectiveOperationPolicy) []string {
	chain := make([]string, 0, len(preview.FallbackChain)+1)
	seen := make(map[string]struct{}, len(preview.FallbackChain)+1)
	if preview.SelectedService != "" {
		chain = append(chain, preview.SelectedService)
		seen[preview.SelectedService] = struct{}{}
	}
	for _, service := range preview.FallbackChain {
		if service == "" {
			continue
		}
		if _, ok := seen[service]; ok {
			continue
		}
		seen[service] = struct{}{}
		chain = append(chain, service)
	}
	if len(chain) == 0 && preview.SelectedService != "" {
		chain = []string{preview.SelectedService}
	}
	if preview.RequestMode == config.ModeSpecified || !policy.AllowFallback {
		if len(chain) > 1 {
			return chain[:1]
		}
		return chain
	}
	if policy.MaxFallbackAttempts > 0 && len(chain) > policy.MaxFallbackAttempts {
		return chain[:policy.MaxFallbackAttempts]
	}
	return chain
}

func (d *Dispatcher) shouldRetry(preview routing.Preview, policy effectiveOperationPolicy, attemptIndex int, retryable bool) bool {
	if preview.RequestMode != config.ModeStrategy || !policy.AllowFallback || !retryable {
		return false
	}
	nextIndex := attemptIndex + 1
	if policy.MaxFallbackAttempts > 0 && nextIndex >= policy.MaxFallbackAttempts {
		return false
	}
	return nextIndex < len(preview.FallbackChain)
}

func (d *Dispatcher) newTrace(req api.Request, preview routing.Preview, _ effectiveOperationPolicy) *RouteTrace {
	if d.traceStore == nil || !d.traceStore.Enabled() {
		return nil
	}
	return &RouteTrace{
		TraceID:             fmt.Sprintf("trace-%d", time.Now().UnixNano()),
		RequestID:           req.ID,
		Operation:           req.Operation,
		RequestMode:         preview.RequestMode,
		StrategyMode:        preview.StrategyMode,
		RequestedService:    preview.RequestedService,
		PreferredLanguage:   preview.PreferredLanguage,
		InitialReason:       preview.Reason,
		FallbackChain:       append([]string(nil), preview.FallbackChain...),
		FallbackEnabled:     preview.FallbackEnabled,
		MaxFallbackAttempts: preview.MaxFallbackAttempts,
		RetryableCategories: append([]string(nil), preview.RetryableCategories...),
		Preview:             preview,
		CreatedAt:           time.Now().UTC(),
	}
}

func finalRouteReason(initial string, attemptIndex int) string {
	if attemptIndex == 0 {
		return initial
	}
	return "fallback_retry_success"
}

func finalFailureReason(initial string, attempts int, trace *RouteTrace) string {
	if attempts <= 1 || trace == nil || len(trace.Attempts) <= 1 {
		return initial
	}
	return "fallback_retry_exhausted"
}

func (d *Dispatcher) isRetryableFailure(record *attribution.Record, policy effectiveOperationPolicy) bool {
	if record == nil {
		return false
	}
	for _, category := range policy.RetryableCategories {
		if category == record.Category {
			return true
		}
	}
	return false
}

func recordCategory(record *attribution.Record) string {
	if record == nil {
		return ""
	}
	return record.Category
}

func recordMessage(record *attribution.Record) string {
	if record == nil {
		return ""
	}
	return record.Message
}

func traceID(trace *RouteTrace) string {
	if trace == nil {
		return ""
	}
	return trace.TraceID
}

func attemptCount(trace *RouteTrace) int {
	if trace == nil {
		return 0
	}
	return len(trace.Attempts)
}

func wasRetried(trace *RouteTrace) bool {
	if trace == nil {
		return false
	}
	return len(trace.Attempts) > 1
}

func (d *Dispatcher) policyFor(operation string) effectiveOperationPolicy {
	d.mu.RLock()
	defer d.mu.RUnlock()
	return d.policyForLocked(operation)
}

func (d *Dispatcher) policyForLocked(operation string) effectiveOperationPolicy {
	policy := effectiveOperationPolicy{
		AllowFallback:       d.enableFallbackRetry,
		MaxFallbackAttempts: d.maxFallbackAttempts,
		RetryableCategories: append([]string(nil), d.retryableCategories...),
	}

	configured, ok := d.operationPolicies[operation]
	if !ok {
		return policy
	}

	switch configured.FallbackMode {
	case "enabled":
		policy.AllowFallback = true
	case "disabled":
		policy.AllowFallback = false
	}
	if configured.MaxFallbackAttempts > 0 {
		policy.MaxFallbackAttempts = configured.MaxFallbackAttempts
	}
	if len(configured.RetryableCategories) > 0 {
		policy.RetryableCategories = append([]string(nil), configured.RetryableCategories...)
	}
	if !policy.AllowFallback {
		policy.MaxFallbackAttempts = 1
	}
	return policy
}

func (d *Dispatcher) applyPolicyToPreview(preview *routing.Preview, policy effectiveOperationPolicy) {
	if preview == nil {
		return
	}
	allowFallback := policy.AllowFallback
	maxAttempts := policy.MaxFallbackAttempts
	if preview.RequestMode == config.ModeSpecified {
		allowFallback = false
		maxAttempts = 1
	}
	preview.FallbackEnabled = allowFallback
	preview.MaxFallbackAttempts = maxAttempts
	preview.RetryableCategories = append([]string(nil), policy.RetryableCategories...)
}

func (d *Dispatcher) resolverForCurrentPolicy() *routing.Resolver {
	d.mu.RLock()
	defer d.mu.RUnlock()
	return routing.New(d.registry, d.cooling, d.selector, d.stats, copyPreferredServices(d.preferredServices))
}
