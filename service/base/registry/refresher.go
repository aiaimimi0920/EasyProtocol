package registry

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"
)

type Refresher interface {
	RefreshAll(ctx context.Context) error
	RefreshService(ctx context.Context, name string) error
}

type HTTPRefresher struct {
	registry *Registry
	client   *http.Client
}

type healthResponse struct {
	Status string `json:"status"`
}

type capabilitiesResponse struct {
	Service    string   `json:"service"`
	Language   string   `json:"language"`
	Operations []string `json:"operations"`
}

func NewHTTPRefresher(reg *Registry) *HTTPRefresher {
	return &HTTPRefresher{
		registry: reg,
		client:   &http.Client{Timeout: 5 * time.Second},
	}
}

func (r *HTTPRefresher) RefreshAll(ctx context.Context) error {
	var lastErr error
	for _, service := range r.registry.List() {
		if err := r.RefreshService(ctx, service.Name); err != nil {
			lastErr = err
		}
	}
	return lastErr
}

func (r *HTTPRefresher) RefreshService(ctx context.Context, name string) error {
	service, ok := r.registry.Get(name)
	if !ok {
		return fmt.Errorf("service %s not found", name)
	}
	if strings.TrimSpace(service.Endpoint) == "" {
		r.registry.UpdateHealth(name, false, "service endpoint is empty", time.Now().UTC())
		return fmt.Errorf("service %s endpoint is empty", name)
	}

	healthURL := strings.TrimRight(service.Endpoint, "/") + "/health"
	capabilitiesURL := strings.TrimRight(service.Endpoint, "/") + "/capabilities"

	healthReq, err := http.NewRequestWithContext(ctx, http.MethodGet, healthURL, nil)
	if err != nil {
		r.registry.UpdateHealth(name, false, fmt.Sprintf("build health request: %v", err), time.Now().UTC())
		return err
	}
	healthResp, err := r.client.Do(healthReq)
	if err != nil {
		r.registry.UpdateHealth(name, false, fmt.Sprintf("health request failed: %v", err), time.Now().UTC())
		return err
	}
	defer healthResp.Body.Close()
	if healthResp.StatusCode >= http.StatusBadRequest {
		msg := fmt.Sprintf("health request returned status %d", healthResp.StatusCode)
		r.registry.UpdateHealth(name, false, msg, time.Now().UTC())
		return errors.New(msg)
	}

	var health healthResponse
	if err := json.NewDecoder(healthResp.Body).Decode(&health); err != nil {
		r.registry.UpdateHealth(name, false, fmt.Sprintf("decode health response: %v", err), time.Now().UTC())
		return err
	}
	if !strings.EqualFold(health.Status, "ok") {
		msg := fmt.Sprintf("service health status is %q", health.Status)
		r.registry.UpdateHealth(name, false, msg, time.Now().UTC())
		return errors.New(msg)
	}

	capReq, err := http.NewRequestWithContext(ctx, http.MethodGet, capabilitiesURL, nil)
	if err != nil {
		r.registry.UpdateHealth(name, false, fmt.Sprintf("build capabilities request: %v", err), time.Now().UTC())
		return err
	}
	capResp, err := r.client.Do(capReq)
	if err != nil {
		r.registry.UpdateHealth(name, false, fmt.Sprintf("capabilities request failed: %v", err), time.Now().UTC())
		return err
	}
	defer capResp.Body.Close()
	if capResp.StatusCode >= http.StatusBadRequest {
		msg := fmt.Sprintf("capabilities request returned status %d", capResp.StatusCode)
		r.registry.UpdateHealth(name, false, msg, time.Now().UTC())
		return errors.New(msg)
	}

	var capabilities capabilitiesResponse
	if err := json.NewDecoder(capResp.Body).Decode(&capabilities); err != nil {
		r.registry.UpdateHealth(name, false, fmt.Sprintf("decode capabilities response: %v", err), time.Now().UTC())
		return err
	}

	r.registry.UpdateCapabilities(name, capabilities.Operations)
	r.registry.UpdateHealth(name, true, "", time.Now().UTC())
	return nil
}
