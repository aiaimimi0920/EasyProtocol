package services

import (
	"context"
	"fmt"
	"net/http"
	"os/exec"
	"testing"
	"time"

	"easy_protocol/config"
	"easy_protocol/registry"
)

func TestProviderProcessPoolStartAcquireAndScaleDown(t *testing.T) {
	originalLauncher := defaultProviderProcessLauncher
	originalHealthWaiter := providerChildHealthWaiter
	originalCapabilitiesFetcher := providerChildCapabilitiesFetcher
	t.Cleanup(func() {
		defaultProviderProcessLauncher = originalLauncher
		providerChildHealthWaiter = originalHealthWaiter
		providerChildCapabilitiesFetcher = originalCapabilitiesFetcher
	})

	nextPort := 18001
	defaultProviderProcessLauncher = func(_ context.Context, family *providerProcessFamily, serviceName string, port int) (*exec.Cmd, string, error) {
		if port <= 0 {
			port = nextPort
			nextPort++
		}
		return &exec.Cmd{}, fmt.Sprintf("http://127.0.0.1:%d", port), nil
	}
	providerChildHealthWaiter = func(_ context.Context, _ *http.Client, _ string) error {
		return nil
	}
	providerChildCapabilitiesFetcher = func(_ context.Context, _ *http.Client, _ string) ([]string, error) {
		return []string{"codex.semantic.step"}, nil
	}

	cfg := config.DefaultConfig()
	cfg.ProviderPool.Providers = map[string]config.ProviderPoolProviderConfig{
		"python": {
			WarmReplicas:         1,
			MaxReplicas:          2,
			IdleScaleDownSeconds: 1 * time.Second,
			AcquireTimeout:       1 * time.Second,
		},
	}

	reg := registry.New()
	reg.Register(registry.NewService("PythonProtocol-001", "python", "http://placeholder", true, []string{"codex.semantic.step"}))

	pool := NewProviderProcessPool(cfg, reg)
	if pool == nil {
		t.Fatal("expected provider process pool")
	}

	if err := pool.Start(context.Background()); err != nil {
		t.Fatalf("start pool: %v", err)
	}
	defer pool.Close()

	if _, ok := reg.Get("PythonProtocol-001"); !ok {
		t.Fatal("expected warm child PythonProtocol-001 to be registered")
	}

	svc1, lease1, err := pool.Acquire(context.Background(), "PythonProtocol-001")
	if err != nil {
		t.Fatalf("acquire first lease: %v", err)
	}
	if svc1 != "PythonProtocol-001" {
		t.Fatalf("expected first lease to use PythonProtocol-001, got %q", svc1)
	}

	svc2, lease2, err := pool.Acquire(context.Background(), "PythonProtocol-001")
	if err != nil {
		t.Fatalf("acquire second lease: %v", err)
	}
	if svc2 != "PythonProtocol-002" {
		t.Fatalf("expected second lease to scale up PythonProtocol-002, got %q", svc2)
	}

	lease1.Release()
	lease2.Release()

	pool.mu.Lock()
	family := pool.families["PythonProtocol"]
	if family == nil {
		pool.mu.Unlock()
		t.Fatal("expected PythonProtocol family")
	}
	if child, ok := family.children["PythonProtocol-002"]; ok {
		child.lastIdleAt = time.Now().Add(-5 * time.Second)
	}
	pool.mu.Unlock()

	pool.reconcileFamily(family)

	if _, ok := reg.Get("PythonProtocol-002"); ok {
		t.Fatal("expected idle surplus child PythonProtocol-002 to be scaled down")
	}
}
