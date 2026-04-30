package transports

import (
	"context"

	"easy_protocol/api"
)

type Result struct {
	Payload  map[string]any
	Metadata map[string]string
}

type ServiceTransport interface {
	Call(ctx context.Context, service string, request api.Request) (Result, error)
}

type ServiceCallError struct {
	Category string
	Message  string
	Details  map[string]any
}

func (e *ServiceCallError) Error() string {
	if e == nil || e.Message == "" {
		return "service call failed"
	}
	return e.Message
}

type StubTransport struct{}

func (StubTransport) Call(_ context.Context, service string, request api.Request) (Result, error) {
	return Result{
		Payload: map[string]any{
			"service":   service,
			"operation": request.Operation,
			"mode":      string(request.Mode),
		},
		Metadata: map[string]string{
			"stub": "true",
		},
	}, nil
}
