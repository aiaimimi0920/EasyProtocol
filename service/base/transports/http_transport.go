package transports

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"easy_protocol/api"
	"easy_protocol/attribution"
	"easy_protocol/registry"
)

type HTTPTransport struct {
	client   *http.Client
	registry *registry.Registry
}

func NewHTTPTransport(reg *registry.Registry) *HTTPTransport {
	return &HTTPTransport{
		client:   &http.Client{Timeout: 10 * time.Minute},
		registry: reg,
	}
}

type serviceResponse struct {
	RequestID string         `json:"request_id"`
	Service   string         `json:"service"`
	Status    string         `json:"status"`
	Result    map[string]any `json:"result,omitempty"`
	Error     *struct {
		Category string         `json:"category"`
		Message  string         `json:"message"`
		Details  map[string]any `json:"details,omitempty"`
	} `json:"error,omitempty"`
}

func (t *HTTPTransport) Call(ctx context.Context, service string, request api.Request) (Result, error) {
	entry, ok := t.registry.Get(service)
	if !ok {
		return Result{}, &ServiceCallError{
			Category: attribution.CategoryServiceNotFound,
			Message:  "service not registered",
		}
	}
	if strings.TrimSpace(entry.Endpoint) == "" {
		return Result{}, &ServiceCallError{
			Category: attribution.CategoryRoutingError,
			Message:  "service endpoint is empty",
		}
	}

	body, err := json.Marshal(request)
	if err != nil {
		return Result{}, &ServiceCallError{
			Category: attribution.CategoryTransportError,
			Message:  fmt.Sprintf("marshal request: %v", err),
		}
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(entry.Endpoint, "/")+"/invoke", bytes.NewReader(body))
	if err != nil {
		return Result{}, &ServiceCallError{
			Category: attribution.CategoryTransportError,
			Message:  fmt.Sprintf("create request: %v", err),
		}
	}
	httpReq.Header.Set("Content-Type", "application/json")

	resp, err := t.client.Do(httpReq)
	if err != nil {
		return Result{}, &ServiceCallError{
			Category: attribution.CategoryTransportError,
			Message:  fmt.Sprintf("invoke service: %v", err),
		}
	}
	defer resp.Body.Close()

	var payload serviceResponse
	if resp.StatusCode >= http.StatusBadRequest {
		if err := json.NewDecoder(resp.Body).Decode(&payload); err == nil && payload.Error != nil {
			details := payload.Error.Details
			if details == nil {
				details = map[string]any{}
			}
			details["http_status"] = resp.StatusCode
			return Result{}, &ServiceCallError{
				Category: payload.Error.Category,
				Message:  payload.Error.Message,
				Details:  details,
			}
		}
		return Result{}, &ServiceCallError{
			Category: attribution.CategoryDelegationError,
			Message:  fmt.Sprintf("service returned status %d", resp.StatusCode),
			Details: map[string]any{
				"http_status": resp.StatusCode,
			},
		}
	}
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		return Result{}, &ServiceCallError{
			Category: attribution.CategoryTransportError,
			Message:  fmt.Sprintf("decode response: %v", err),
		}
	}

	if strings.EqualFold(payload.Status, "failed") && payload.Error != nil {
		return Result{}, &ServiceCallError{
			Category: payload.Error.Category,
			Message:  payload.Error.Message,
			Details:  payload.Error.Details,
		}
	}

	return Result{
		Payload: payload.Result,
		Metadata: map[string]string{
			"service": payload.Service,
		},
	}, nil
}
