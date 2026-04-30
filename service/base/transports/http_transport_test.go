package transports

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"easy_protocol/api"
	"easy_protocol/attribution"
	"easy_protocol/registry"
)

func TestHTTPTransportPreservesServiceErrorOnHTTPFailure(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte(`{"status":"failed","error":{"category":"unsupported_operation","message":"service does not support operation"}}`))
	}))
	defer backend.Close()

	reg := registry.New()
	reg.Register(registry.NewService("JSProtocol", "javascript", backend.URL, true, []string{"protocol.echo"}))
	transport := NewHTTPTransport(reg)

	_, err := transport.Call(context.Background(), "JSProtocol", api.Request{Operation: "protocol.regex.extract"})
	if err == nil {
		t.Fatalf("expected service call error")
	}
	callErr, ok := err.(*ServiceCallError)
	if !ok {
		t.Fatalf("expected ServiceCallError, got %T", err)
	}
	if callErr.Category != attribution.CategoryUnsupportedOp {
		t.Fatalf("expected %s, got %s", attribution.CategoryUnsupportedOp, callErr.Category)
	}
	if callErr.Details["http_status"] != http.StatusBadRequest {
		t.Fatalf("expected http status detail to be preserved, got %#v", callErr.Details)
	}
}
