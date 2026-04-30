package api

import (
	"easy_protocol/attribution"
	"easy_protocol/config"
)

type Request struct {
	ID               string               `json:"request_id"`
	Mode             config.ExecutionMode `json:"mode"`
	Operation        string               `json:"operation"`
	Payload          map[string]any       `json:"payload,omitempty"`
	RequestedService string               `json:"requested_service,omitempty"`
	RoutingHints     map[string]string    `json:"routing_hints,omitempty"`
}

type ResponseStatus string

const (
	StatusSucceeded ResponseStatus = "succeeded"
	StatusFailed    ResponseStatus = "failed"
)

type ResponseMeta struct {
	RequestMode     config.ExecutionMode `json:"request_mode"`
	StrategyMode    config.SelectorMode  `json:"strategy_mode,omitempty"`
	RouteReason     string               `json:"route_reason,omitempty"`
	FallbackChain   []string             `json:"fallback_chain,omitempty"`
	TraceID         string               `json:"trace_id,omitempty"`
	AttemptCount    int                  `json:"attempt_count"`
	Retried         bool                 `json:"retried"`
	CooldownApplied bool                 `json:"cooldown_applied"`
}

type Response struct {
	RequestID       string              `json:"request_id"`
	SelectedService string              `json:"selected_service,omitempty"`
	Status          ResponseStatus      `json:"status"`
	Result          map[string]any      `json:"result,omitempty"`
	Error           *attribution.Record `json:"error,omitempty"`
	Meta            ResponseMeta        `json:"meta"`
}
