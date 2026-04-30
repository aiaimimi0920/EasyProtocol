package registry

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHTTPRefresherUpdatesHealthAndCapabilities(t *testing.T) {
	handler := http.NewServeMux()
	handler.HandleFunc("/health", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})
	handler.HandleFunc("/capabilities", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"service":"JSProtocol","language":"javascript","operations":["health.inspect","protocol.echo"]}`))
	})
	srv := httptest.NewServer(handler)
	defer srv.Close()

	reg := New()
	reg.Register(NewService("JSProtocol", "javascript", srv.URL, true, nil))
	refresher := NewHTTPRefresher(reg)

	if err := refresher.RefreshAll(context.Background()); err != nil {
		t.Fatalf("refresh all failed: %v", err)
	}

	service, ok := reg.Get("JSProtocol")
	if !ok {
		t.Fatalf("service not found after refresh")
	}
	if !service.HealthKnown || !service.Healthy {
		t.Fatalf("expected service to be marked healthy after refresh")
	}
	if !service.Supports("protocol.echo") {
		t.Fatalf("expected capabilities to be refreshed")
	}
}
