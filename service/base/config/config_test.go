package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadUsesYamlOverridesAndKeepsDefaults(t *testing.T) {
	t.Parallel()

	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")
	content := `mode: specified
unified_api:
  listen: 0.0.0.0:19788
control_plane:
  read_token: "read-token"
strategy:
  max_fallback_attempts: 5
persistence:
  runtime_state_path: /var/lib/easy-protocol/runtime-overrides.json
services: []
`
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("write config: %v", err)
	}

	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("load config: %v", err)
	}

	if cfg.Mode != ModeSpecified {
		t.Fatalf("expected specified mode, got %q", cfg.Mode)
	}
	if cfg.UnifiedAPI.Listen != "0.0.0.0:19788" {
		t.Fatalf("unexpected listen: %q", cfg.UnifiedAPI.Listen)
	}
	if cfg.ControlPlane.ReadToken != "read-token" {
		t.Fatalf("unexpected read token: %q", cfg.ControlPlane.ReadToken)
	}
	if cfg.ControlPlane.MutateToken != "123456" {
		t.Fatalf("expected omitted mutate token to preserve default value, got %q", cfg.ControlPlane.MutateToken)
	}
	if cfg.Strategy.MaxFallbackAttempts != 5 {
		t.Fatalf("unexpected max fallback attempts: %d", cfg.Strategy.MaxFallbackAttempts)
	}
	if cfg.Persistence.RuntimeStatePath != "/var/lib/easy-protocol/runtime-overrides.json" {
		t.Fatalf("unexpected runtime state path: %q", cfg.Persistence.RuntimeStatePath)
	}
	if len(cfg.Services) != 0 {
		t.Fatalf("expected explicit empty services list, got %d", len(cfg.Services))
	}
	if cfg.Tracing.HistoryLimit != 200 {
		t.Fatalf("expected default tracing history limit to remain, got %d", cfg.Tracing.HistoryLimit)
	}
}

func TestLoadEmptyPathFallsBackToDefaultConfig(t *testing.T) {
	t.Parallel()

	cfg, err := Load("")
	if err != nil {
		t.Fatalf("load default config: %v", err)
	}
	if cfg.UnifiedAPI.Listen == "" {
		t.Fatalf("expected default listen address")
	}
	if len(cfg.Services) == 0 {
		t.Fatalf("expected default services to be present")
	}
}
