package services

import (
	"context"
	"sort"
	"strings"

	"easy_protocol/api"
	"easy_protocol/config"
	"easy_protocol/routing"
	"easy_protocol/strategy"
)

type PolicySnapshot struct {
	Operation           string                       `json:"operation"`
	SelectorMode        config.SelectorMode          `json:"selector_mode"`
	PreferredServices   []string                     `json:"preferred_services,omitempty"`
	FallbackEnabled     bool                         `json:"fallback_enabled"`
	MaxFallbackAttempts int                          `json:"max_fallback_attempts"`
	RetryableCategories []string                     `json:"retryable_categories,omitempty"`
	UsesOperationPolicy bool                         `json:"uses_operation_policy"`
	OperationPolicy     config.OperationPolicyConfig `json:"operation_policy,omitempty"`
}

type PolicyReport struct {
	Global     PolicySnapshot   `json:"global"`
	Operations []PolicySnapshot `json:"operations,omitempty"`
}

type PolicyUpdateRequest struct {
	Actor      string                  `json:"actor,omitempty"`
	Reason     string                  `json:"reason,omitempty"`
	Global     *GlobalPolicyPatch      `json:"global,omitempty"`
	Operations []OperationPolicyUpdate `json:"operations,omitempty"`
}

type GlobalPolicyPatch struct {
	FallbackOnRetryableErrors *bool    `json:"fallback_on_retryable_errors,omitempty"`
	MaxFallbackAttempts       *int     `json:"max_fallback_attempts,omitempty"`
	RetryableCategories       []string `json:"retryable_categories,omitempty"`
}

type OperationPolicyUpdate struct {
	Operation              string   `json:"operation"`
	PreferredServices      []string `json:"preferred_services,omitempty"`
	ResetPreferredServices bool     `json:"reset_preferred_services,omitempty"`
	FallbackMode           *string  `json:"fallback_mode,omitempty"`
	MaxFallbackAttempts    *int     `json:"max_fallback_attempts,omitempty"`
	RetryableCategories    []string `json:"retryable_categories,omitempty"`
	ResetPolicy            bool     `json:"reset_policy,omitempty"`
}

type RouteSimulationOverride struct {
	PreferredServices   []string `json:"preferred_services,omitempty"`
	FallbackMode        string   `json:"fallback_mode,omitempty"`
	MaxFallbackAttempts int      `json:"max_fallback_attempts,omitempty"`
	RetryableCategories []string `json:"retryable_categories,omitempty"`
}

type RouteSimulationRequest struct {
	Request  api.Request             `json:"request"`
	Override RouteSimulationOverride `json:"override"`
}

type RouteSimulationResult struct {
	Request          api.Request     `json:"request"`
	BaselinePolicy   PolicySnapshot  `json:"baseline_policy"`
	BaselinePreview  routing.Preview `json:"baseline_preview"`
	SimulatedPolicy  PolicySnapshot  `json:"simulated_policy"`
	SimulatedPreview routing.Preview `json:"simulated_preview"`
	Differences      map[string]bool `json:"differences"`
}

func (d *Dispatcher) PolicyReport(ctx context.Context) (PolicyReport, error) {
	if d.refresher != nil {
		_ = d.refresher.RefreshAll(ctx)
	}
	d.mu.RLock()
	globalFallbackEnabled := d.enableFallbackRetry
	globalMaxAttempts := d.maxFallbackAttempts
	globalRetryable := append([]string(nil), d.retryableCategories...)
	selectorMode := d.strategyMode
	d.mu.RUnlock()

	report := PolicyReport{
		Global: PolicySnapshot{
			Operation:           "*",
			SelectorMode:        selectorMode,
			FallbackEnabled:     globalFallbackEnabled,
			MaxFallbackAttempts: globalMaxAttempts,
			RetryableCategories: globalRetryable,
		},
	}

	operations := d.knownOperations()
	items := make([]PolicySnapshot, 0, len(operations))
	for _, operation := range operations {
		items = append(items, d.PolicySnapshot(operation))
	}
	report.Operations = items
	return report, nil
}

func (d *Dispatcher) SnapshotRuntimePolicyState() RuntimePolicyState {
	d.mu.RLock()
	defer d.mu.RUnlock()

	operations := make(map[string]RuntimeOperationPolicyState, len(d.preferredServices)+len(d.operationPolicies))
	seen := make(map[string]struct{})
	for operation := range d.preferredServices {
		seen[operation] = struct{}{}
	}
	for operation := range d.operationPolicies {
		seen[operation] = struct{}{}
	}
	for operation := range seen {
		operations[operation] = RuntimeOperationPolicyState{
			PreferredServices: append([]string(nil), d.preferredServices[operation]...),
			Policy:            d.operationPolicies[operation],
		}
	}

	return RuntimePolicyState{
		Global: RuntimeGlobalPolicyState{
			FallbackOnRetryableErrors: d.enableFallbackRetry,
			MaxFallbackAttempts:       d.maxFallbackAttempts,
			RetryableCategories:       append([]string(nil), d.retryableCategories...),
		},
		Operations: operations,
	}
}

func (d *Dispatcher) ApplyRuntimePolicyState(state RuntimePolicyState) {
	d.mu.Lock()
	defer d.mu.Unlock()

	d.enableFallbackRetry = state.Global.FallbackOnRetryableErrors
	if state.Global.MaxFallbackAttempts > 0 {
		d.maxFallbackAttempts = state.Global.MaxFallbackAttempts
	}
	if state.Global.RetryableCategories != nil {
		d.retryableCategories = normalizeStringList(state.Global.RetryableCategories)
	}

	preferred := make(map[string][]string, len(state.Operations))
	policies := make(map[string]config.OperationPolicyConfig, len(state.Operations))
	for operation, item := range state.Operations {
		normalizedOperation := strings.TrimSpace(operation)
		if normalizedOperation == "" {
			continue
		}
		if len(item.PreferredServices) > 0 {
			preferred[normalizedOperation] = normalizeStringList(item.PreferredServices)
		}
		policy := item.Policy
		policy.FallbackMode = strings.ToLower(strings.TrimSpace(policy.FallbackMode))
		policy.RetryableCategories = normalizeStringList(policy.RetryableCategories)
		if policy.FallbackMode != "" || policy.MaxFallbackAttempts > 0 || len(policy.RetryableCategories) > 0 {
			policies[normalizedOperation] = policy
		}
	}
	d.preferredServices = preferred
	d.operationPolicies = policies
}

func (d *Dispatcher) PolicySnapshot(operation string) PolicySnapshot {
	d.mu.RLock()
	defer d.mu.RUnlock()
	return d.policySnapshotLocked(operation)
}

func (d *Dispatcher) policySnapshotLocked(operation string) PolicySnapshot {
	effective := d.policyForLocked(operation)
	raw, hasRaw := d.operationPolicies[operation]
	return PolicySnapshot{
		Operation:           operation,
		SelectorMode:        d.strategyMode,
		PreferredServices:   append([]string(nil), d.preferredServices[operation]...),
		FallbackEnabled:     effective.AllowFallback,
		MaxFallbackAttempts: effective.MaxFallbackAttempts,
		RetryableCategories: append([]string(nil), effective.RetryableCategories...),
		UsesOperationPolicy: hasRaw,
		OperationPolicy:     raw,
	}
}

func (d *Dispatcher) SimulateRoute(ctx context.Context, request RouteSimulationRequest) (RouteSimulationResult, error) {
	normalized := d.normalizeRequest(request.Request)
	if d.refresher != nil {
		_ = d.refresher.RefreshAll(ctx)
	}

	baselinePreview := d.resolverForCurrentPolicy().Preview(normalized)
	baselinePolicy := d.PolicySnapshot(normalized.Operation)
	d.applyPolicyToPreview(&baselinePreview, d.policyFor(normalized.Operation))

	simulatedPolicy := d.policySnapshotWithOverride(normalized.Operation, request.Override)
	simulatedResolver := routing.New(
		d.registry,
		d.cooling,
		strategy.New(d.strategyMode),
		d.stats,
		d.preferredServicesWithOverride(normalized.Operation, request.Override.PreferredServices),
	)
	simulatedPreview := simulatedResolver.Preview(normalized)
	d.applyPolicyToPreview(&simulatedPreview, d.policyForOverride(normalized.Operation, request.Override))

	return RouteSimulationResult{
		Request:          normalized,
		BaselinePolicy:   baselinePolicy,
		BaselinePreview:  baselinePreview,
		SimulatedPolicy:  simulatedPolicy,
		SimulatedPreview: simulatedPreview,
		Differences: map[string]bool{
			"selected_service_changed":   baselinePreview.SelectedService != simulatedPreview.SelectedService,
			"route_reason_changed":       baselinePreview.Reason != simulatedPreview.Reason,
			"fallback_chain_changed":     !sameStrings(baselinePreview.FallbackChain, simulatedPreview.FallbackChain),
			"fallback_policy_changed":    baselinePolicy.FallbackEnabled != simulatedPolicy.FallbackEnabled || baselinePolicy.MaxFallbackAttempts != simulatedPolicy.MaxFallbackAttempts || !sameStrings(baselinePolicy.RetryableCategories, simulatedPolicy.RetryableCategories),
			"preferred_services_changed": !sameStrings(baselinePolicy.PreferredServices, simulatedPolicy.PreferredServices),
		},
	}, nil
}

func (d *Dispatcher) UpdatePolicies(req PolicyUpdateRequest) (PolicyReport, error) {
	d.mu.Lock()
	if req.Global != nil {
		if req.Global.FallbackOnRetryableErrors != nil {
			d.enableFallbackRetry = *req.Global.FallbackOnRetryableErrors
		}
		if req.Global.MaxFallbackAttempts != nil && *req.Global.MaxFallbackAttempts > 0 {
			d.maxFallbackAttempts = *req.Global.MaxFallbackAttempts
		}
		if req.Global.RetryableCategories != nil {
			d.retryableCategories = normalizeStringList(req.Global.RetryableCategories)
		}
	}
	for _, update := range req.Operations {
		operation := strings.TrimSpace(update.Operation)
		if operation == "" {
			continue
		}
		if update.ResetPreferredServices {
			delete(d.preferredServices, operation)
		}
		if update.PreferredServices != nil {
			normalizedPreferred := normalizeStringList(update.PreferredServices)
			if len(normalizedPreferred) == 0 {
				delete(d.preferredServices, operation)
			} else {
				d.preferredServices[operation] = normalizedPreferred
			}
		}

		if update.ResetPolicy {
			delete(d.operationPolicies, operation)
		}
		if update.FallbackMode != nil || update.MaxFallbackAttempts != nil || update.RetryableCategories != nil {
			policy := d.operationPolicies[operation]
			if update.FallbackMode != nil {
				policy.FallbackMode = strings.ToLower(strings.TrimSpace(*update.FallbackMode))
			}
			if update.MaxFallbackAttempts != nil {
				policy.MaxFallbackAttempts = *update.MaxFallbackAttempts
			}
			if update.RetryableCategories != nil {
				policy.RetryableCategories = normalizeStringList(update.RetryableCategories)
			}
			d.operationPolicies[operation] = policy
		}
	}
	d.mu.Unlock()
	return d.PolicyReport(context.Background())
}

func (d *Dispatcher) policySnapshotWithOverride(operation string, override RouteSimulationOverride) PolicySnapshot {
	policy := d.policyForOverride(operation, override)
	d.mu.RLock()
	raw, hasRaw := d.operationPolicies[operation]
	preferred := d.preferredServices[operation]
	d.mu.RUnlock()
	if len(override.PreferredServices) > 0 {
		preferred = normalizeStringList(override.PreferredServices)
	}
	return PolicySnapshot{
		Operation:           operation,
		SelectorMode:        d.strategyMode,
		PreferredServices:   append([]string(nil), preferred...),
		FallbackEnabled:     policy.AllowFallback,
		MaxFallbackAttempts: policy.MaxFallbackAttempts,
		RetryableCategories: append([]string(nil), policy.RetryableCategories...),
		UsesOperationPolicy: hasRaw || hasSimulationOverride(override),
		OperationPolicy:     raw,
	}
}

func (d *Dispatcher) policyForOverride(operation string, override RouteSimulationOverride) effectiveOperationPolicy {
	policy := d.policyFor(operation)
	switch strings.ToLower(strings.TrimSpace(override.FallbackMode)) {
	case "enabled":
		policy.AllowFallback = true
	case "disabled":
		policy.AllowFallback = false
	}
	if override.MaxFallbackAttempts > 0 {
		policy.MaxFallbackAttempts = override.MaxFallbackAttempts
	}
	if len(override.RetryableCategories) > 0 {
		policy.RetryableCategories = normalizeStringList(override.RetryableCategories)
	}
	if !policy.AllowFallback {
		policy.MaxFallbackAttempts = 1
	}
	return policy
}

func (d *Dispatcher) preferredServicesWithOverride(operation string, override []string) map[string][]string {
	d.mu.RLock()
	out := copyPreferredServices(d.preferredServices)
	d.mu.RUnlock()
	if len(override) > 0 {
		out[operation] = normalizeStringList(override)
	}
	return out
}

func (d *Dispatcher) knownOperations() []string {
	d.mu.RLock()
	defer d.mu.RUnlock()
	seen := make(map[string]struct{})
	for operation := range d.preferredServices {
		if strings.TrimSpace(operation) != "" {
			seen[operation] = struct{}{}
		}
	}
	for operation := range d.operationPolicies {
		if strings.TrimSpace(operation) != "" {
			seen[operation] = struct{}{}
		}
	}
	if d.registry != nil {
		for _, service := range d.registry.List() {
			for _, operation := range service.SupportedOperations {
				if strings.TrimSpace(operation) != "" {
					seen[operation] = struct{}{}
				}
			}
		}
	}
	out := make([]string, 0, len(seen))
	for operation := range seen {
		out = append(out, operation)
	}
	sort.Strings(out)
	return out
}

func copyPreferredServices(source map[string][]string) map[string][]string {
	out := make(map[string][]string, len(source))
	for operation, services := range source {
		out[operation] = append([]string(nil), services...)
	}
	return out
}

func normalizeStringList(items []string) []string {
	seen := make(map[string]struct{}, len(items))
	out := make([]string, 0, len(items))
	for _, item := range items {
		normalized := strings.TrimSpace(item)
		if normalized == "" {
			continue
		}
		if _, ok := seen[normalized]; ok {
			continue
		}
		seen[normalized] = struct{}{}
		out = append(out, normalized)
	}
	return out
}

func sameStrings(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	for idx := range left {
		if left[idx] != right[idx] {
			return false
		}
	}
	return true
}

func hasSimulationOverride(override RouteSimulationOverride) bool {
	return len(override.PreferredServices) > 0 ||
		strings.TrimSpace(override.FallbackMode) != "" ||
		override.MaxFallbackAttempts > 0 ||
		len(override.RetryableCategories) > 0
}
