package attribution

const (
	CategoryValidationError    = "validation_error"
	CategoryUnsupportedOp      = "unsupported_operation"
	CategoryNoServiceAvail     = "no_service_available"
	CategoryServiceNotFound    = "service_not_found"
	CategoryServiceDisabled    = "service_disabled"
	CategoryServiceUnavailable = "service_unavailable"
	CategoryServiceCooled      = "service_cooled"
	CategoryMaintenanceMode    = "maintenance_mode"
	CategoryRoutingError       = "routing_error"
	CategoryDelegationError    = "delegation_error"
	CategoryTransportError     = "transport_error"
	CategoryServiceRuntimeErr  = "service_runtime_error"
	CategoryTimeoutError       = "timeout_error"
)

type Record struct {
	RequestID           string         `json:"request_id"`
	Service             string         `json:"service,omitempty"`
	Category            string         `json:"category"`
	CountsTowardCooling bool           `json:"counts_toward_cooling"`
	Message             string         `json:"message"`
	Details             map[string]any `json:"details,omitempty"`
}

func CountsTowardCooling(category string) bool {
	switch category {
	case CategoryDelegationError, CategoryTransportError, CategoryTimeoutError:
		return true
	default:
		return false
	}
}

func NewRecord(requestID, service, category, message string, details map[string]any) Record {
	if details == nil {
		details = map[string]any{}
	}
	return Record{
		RequestID:           requestID,
		Service:             service,
		Category:            category,
		CountsTowardCooling: CountsTowardCooling(category),
		Message:             message,
		Details:             details,
	}
}
