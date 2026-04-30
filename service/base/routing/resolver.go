package routing

import (
	"strings"
	"time"

	"easy_protocol/api"
	"easy_protocol/attribution"
	"easy_protocol/config"
	"easy_protocol/cooling"
	"easy_protocol/registry"
	"easy_protocol/stats"
	"easy_protocol/strategy"
)

type ResolutionError struct {
	Category string
	Service  string
	Message  string
}

func (e *ResolutionError) Error() string {
	return e.Message
}

type Decision struct {
	RequestID           string
	RequestMode         config.ExecutionMode
	StrategyMode        config.SelectorMode
	RequestedService    string
	SelectedService     string
	SelectionMode       string
	Reason              string
	NormalizedOperation string
	CooldownApplied     bool
	FallbackChain       []string
}

type PreviewError struct {
	Category string `json:"category"`
	Service  string `json:"service,omitempty"`
	Message  string `json:"message"`
}

type PreviewCandidate struct {
	Service           string `json:"service"`
	Language          string `json:"language"`
	Enabled           bool   `json:"enabled"`
	HealthKnown       bool   `json:"health_known"`
	Healthy           bool   `json:"healthy"`
	SupportsOperation bool   `json:"supports_operation"`
	Cooled            bool   `json:"cooled"`
	ActiveRequests    int64  `json:"active_requests"`
	Eligible          bool   `json:"eligible"`
	InSelectionScope  bool   `json:"in_selection_scope"`
	Selected          bool   `json:"selected"`
	PreferenceRank    int    `json:"preference_rank,omitempty"`
	ExclusionReason   string `json:"exclusion_reason,omitempty"`
}

type Preview struct {
	RequestID           string               `json:"request_id"`
	RequestMode         config.ExecutionMode `json:"request_mode"`
	StrategyMode        config.SelectorMode  `json:"strategy_mode,omitempty"`
	RequestedService    string               `json:"requested_service,omitempty"`
	PreferredLanguage   string               `json:"preferred_language,omitempty"`
	PreferredServices   []string             `json:"preferred_services,omitempty"`
	FallbackEnabled     bool                 `json:"fallback_enabled"`
	MaxFallbackAttempts int                  `json:"max_fallback_attempts"`
	RetryableCategories []string             `json:"retryable_categories,omitempty"`
	SelectedService     string               `json:"selected_service,omitempty"`
	SelectionMode       string               `json:"selection_mode,omitempty"`
	Reason              string               `json:"reason,omitempty"`
	NormalizedOperation string               `json:"normalized_operation,omitempty"`
	FallbackChain       []string             `json:"fallback_chain,omitempty"`
	Candidates          []PreviewCandidate   `json:"candidates,omitempty"`
	Error               *PreviewError        `json:"error,omitempty"`
}

type Resolver struct {
	registry                     *registry.Registry
	cooling                      *cooling.Manager
	selector                     strategy.Selector
	stats                        *stats.Manager
	preferredServicesByOperation map[string][]string
}

func New(
	reg *registry.Registry,
	coolingMgr *cooling.Manager,
	selector strategy.Selector,
	statsMgr *stats.Manager,
	preferredServicesByOperation map[string][]string,
) *Resolver {
	return &Resolver{
		registry:                     reg,
		cooling:                      coolingMgr,
		selector:                     selector,
		stats:                        statsMgr,
		preferredServicesByOperation: preferredServicesByOperation,
	}
}

func (r *Resolver) Resolve(req api.Request) (Decision, error) {
	preview := r.Preview(req)
	if preview.Error != nil {
		return Decision{}, &ResolutionError{
			Category: preview.Error.Category,
			Service:  preview.Error.Service,
			Message:  preview.Error.Message,
		}
	}

	return Decision{
		RequestID:           preview.RequestID,
		RequestMode:         preview.RequestMode,
		StrategyMode:        preview.StrategyMode,
		RequestedService:    preview.RequestedService,
		SelectedService:     preview.SelectedService,
		SelectionMode:       preview.SelectionMode,
		Reason:              preview.Reason,
		NormalizedOperation: preview.NormalizedOperation,
		FallbackChain:       append([]string(nil), preview.FallbackChain...),
	}, nil
}

func (r *Resolver) Preview(req api.Request) Preview {
	operation := strings.TrimSpace(req.Operation)
	mode := req.Mode
	if mode == "" {
		mode = config.ModeStrategy
	}

	preview := Preview{
		RequestID:           req.ID,
		RequestMode:         mode,
		StrategyMode:        r.selector.Mode(),
		RequestedService:    strings.TrimSpace(req.RequestedService),
		PreferredLanguage:   strings.TrimSpace(req.RoutingHints["preferred_language"]),
		PreferredServices:   append([]string(nil), r.preferredServicesByOperation[operation]...),
		NormalizedOperation: operation,
		Candidates:          r.evaluateCandidates(operation),
	}

	if operation == "" {
		preview.Error = &PreviewError{
			Category: attribution.CategoryValidationError,
			Message:  "operation is required",
		}
		return preview
	}

	switch mode {
	case config.ModeSpecified:
		return r.previewSpecified(preview)
	default:
		return r.previewStrategy(preview)
	}
}

func (r *Resolver) previewSpecified(preview Preview) Preview {
	if preview.RequestedService == "" {
		preview.Error = &PreviewError{
			Category: attribution.CategoryValidationError,
			Message:  "requested_service is required in specified mode",
		}
		return preview
	}

	candidateIndex := -1
	for idx, candidate := range preview.Candidates {
		if candidate.Service == preview.RequestedService {
			candidateIndex = idx
			break
		}
	}

	if candidateIndex == -1 {
		preview.Error = &PreviewError{
			Category: attribution.CategoryServiceNotFound,
			Service:  preview.RequestedService,
			Message:  "requested service is not registered",
		}
		return preview
	}

	preview.Candidates[candidateIndex].InSelectionScope = true
	preview.FallbackChain = []string{preview.RequestedService}

	if !preview.Candidates[candidateIndex].Eligible {
		preview.Error = &PreviewError{
			Category: mapExclusionToCategory(preview.Candidates[candidateIndex].ExclusionReason),
			Service:  preview.RequestedService,
			Message:  mapExclusionToMessage(preview.Candidates[candidateIndex].ExclusionReason),
		}
		return preview
	}

	preview.SelectedService = preview.RequestedService
	preview.SelectionMode = "specified"
	preview.Reason = "caller_selected_service"
	preview.Candidates[candidateIndex].Selected = true
	return preview
}

func (r *Resolver) previewStrategy(preview Preview) Preview {
	eligible, indexes := eligibleCandidates(preview.Candidates)
	if len(eligible) == 0 {
		preview.Error = &PreviewError{
			Category: attribution.CategoryNoServiceAvail,
			Message:  "no eligible service available for operation",
		}
		return preview
	}

	scope := eligible
	scopeIndexes := indexes
	preview.SelectionMode = string(r.selector.Mode())
	preview.Reason = "strategy_selector"

	if preview.PreferredLanguage != "" {
		narrowed, narrowedIndexes := narrowByPreferredLanguage(scope, scopeIndexes, preview.PreferredLanguage)
		if len(narrowed) > 0 {
			scope = narrowed
			scopeIndexes = narrowedIndexes
			preview.Reason = "strategy_selector_preferred_language"
		}
	}

	ordered := strategy.Ordered(r.selector.Mode(), toStrategyCandidates(scope))
	if preview.PreferredLanguage == "" && len(preview.PreferredServices) > 0 {
		ordered = orderWithPreference(ordered, preview.PreferredServices)
		if containsPreferred(ordered, preview.PreferredServices) {
			preview.Reason = "strategy_selector_preferred_service"
		}
	}

	if len(ordered) == 0 {
		preview.Error = &PreviewError{
			Category: attribution.CategoryNoServiceAvail,
			Message:  "no eligible service available for operation",
		}
		return preview
	}

	preview.FallbackChain = candidateNames(ordered)
	scopeSet := make(map[string]struct{}, len(ordered))
	for _, candidate := range ordered {
		scopeSet[candidate.Service] = struct{}{}
	}
	for idx := range preview.Candidates {
		if _, ok := scopeSet[preview.Candidates[idx].Service]; ok {
			preview.Candidates[idx].InSelectionScope = true
		}
	}

	if preview.Reason == "strategy_selector_preferred_service" {
		preview.SelectedService = ordered[0].Service
	} else {
		selected, err := r.selector.Select(ordered)
		if err != nil {
			preview.Error = &PreviewError{
				Category: attribution.CategoryRoutingError,
				Message:  err.Error(),
			}
			return preview
		}
		preview.SelectedService = selected.Service
	}

	for idx := range preview.Candidates {
		if preview.Candidates[idx].Service == preview.SelectedService {
			preview.Candidates[idx].Selected = true
			break
		}
	}
	return preview
}

func (r *Resolver) evaluateCandidates(operation string) []PreviewCandidate {
	services := r.registry.List()
	out := make([]PreviewCandidate, 0, len(services))
	now := time.Now().UTC()
	preferenceRank := buildPreferenceRank(r.preferredServicesByOperation[operation])

	for _, service := range services {
		active := int64(0)
		if r.stats != nil {
			active = r.stats.Snapshot(service.Name).ActiveRequests
		}
		candidate := PreviewCandidate{
			Service:           service.Name,
			Language:          service.Language,
			Enabled:           service.Enabled,
			HealthKnown:       service.HealthKnown,
			Healthy:           service.Healthy,
			SupportsOperation: operation != "" && service.Supports(operation),
			ActiveRequests:    active,
			PreferenceRank:    preferenceRank[service.Name],
		}
		if operation == "" {
			candidate.ExclusionReason = attribution.CategoryValidationError
			out = append(out, candidate)
			continue
		}

		if r.cooling != nil {
			candidate.Cooled = r.cooling.IsCooled(service.Name, now)
		}

		switch {
		case !service.Enabled:
			candidate.ExclusionReason = attribution.CategoryServiceDisabled
		case !candidate.SupportsOperation:
			candidate.ExclusionReason = attribution.CategoryUnsupportedOp
		case service.HealthKnown && !service.Healthy:
			candidate.ExclusionReason = attribution.CategoryServiceUnavailable
		case candidate.Cooled:
			candidate.ExclusionReason = attribution.CategoryServiceCooled
		default:
			candidate.Eligible = true
		}
		out = append(out, candidate)
	}

	return out
}

func eligibleCandidates(candidates []PreviewCandidate) ([]PreviewCandidate, []int) {
	eligible := make([]PreviewCandidate, 0, len(candidates))
	indexes := make([]int, 0, len(candidates))
	for idx, candidate := range candidates {
		if !candidate.Eligible {
			continue
		}
		eligible = append(eligible, candidate)
		indexes = append(indexes, idx)
	}
	return eligible, indexes
}

func narrowByPreferredLanguage(candidates []PreviewCandidate, indexes []int, preferredLanguage string) ([]PreviewCandidate, []int) {
	narrowed := make([]PreviewCandidate, 0, len(candidates))
	narrowedIndexes := make([]int, 0, len(candidates))
	for idx, candidate := range candidates {
		if strings.EqualFold(candidate.Language, preferredLanguage) {
			narrowed = append(narrowed, candidate)
			narrowedIndexes = append(narrowedIndexes, indexes[idx])
		}
	}
	return narrowed, narrowedIndexes
}

func toStrategyCandidates(candidates []PreviewCandidate) []strategy.Candidate {
	out := make([]strategy.Candidate, 0, len(candidates))
	for _, candidate := range candidates {
		out = append(out, strategy.Candidate{
			Service:        candidate.Service,
			ActiveRequests: candidate.ActiveRequests,
		})
	}
	return out
}

func orderWithPreference(candidates []strategy.Candidate, preferredServices []string) []strategy.Candidate {
	if len(preferredServices) == 0 {
		return candidates
	}
	byName := make(map[string]strategy.Candidate, len(candidates))
	for _, candidate := range candidates {
		byName[candidate.Service] = candidate
	}
	ordered := make([]strategy.Candidate, 0, len(candidates))
	seen := make(map[string]struct{}, len(candidates))
	for _, preferredService := range preferredServices {
		candidate, ok := byName[preferredService]
		if !ok {
			continue
		}
		ordered = append(ordered, candidate)
		seen[preferredService] = struct{}{}
	}
	for _, candidate := range candidates {
		if _, ok := seen[candidate.Service]; ok {
			continue
		}
		ordered = append(ordered, candidate)
	}
	return ordered
}

func containsPreferred(candidates []strategy.Candidate, preferredServices []string) bool {
	preferredSet := make(map[string]struct{}, len(preferredServices))
	for _, service := range preferredServices {
		preferredSet[service] = struct{}{}
	}
	for _, candidate := range candidates {
		if _, ok := preferredSet[candidate.Service]; ok {
			return true
		}
	}
	return false
}

func candidateNames(candidates []strategy.Candidate) []string {
	out := make([]string, 0, len(candidates))
	for _, candidate := range candidates {
		out = append(out, candidate.Service)
	}
	return out
}

func buildPreferenceRank(preferredServices []string) map[string]int {
	ranks := make(map[string]int, len(preferredServices))
	for idx, service := range preferredServices {
		ranks[service] = idx + 1
	}
	return ranks
}

func mapExclusionToCategory(exclusion string) string {
	switch exclusion {
	case attribution.CategoryServiceDisabled:
		return attribution.CategoryServiceDisabled
	case attribution.CategoryUnsupportedOp:
		return attribution.CategoryUnsupportedOp
	case attribution.CategoryServiceUnavailable:
		return attribution.CategoryServiceUnavailable
	case attribution.CategoryServiceCooled:
		return attribution.CategoryServiceCooled
	default:
		return attribution.CategoryRoutingError
	}
}

func mapExclusionToMessage(exclusion string) string {
	switch exclusion {
	case attribution.CategoryServiceDisabled:
		return "requested service is disabled"
	case attribution.CategoryUnsupportedOp:
		return "requested service does not support operation"
	case attribution.CategoryServiceUnavailable:
		return "requested service is unavailable"
	case attribution.CategoryServiceCooled:
		return "requested service is currently cooled"
	default:
		return "requested service is not available"
	}
}
