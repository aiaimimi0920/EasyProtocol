package config

import (
	"bytes"
	"fmt"
	"os"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

type ExecutionMode string

const (
	ModeStrategy  ExecutionMode = "strategy"
	ModeSpecified ExecutionMode = "specified"
)

type SelectorMode string

const (
	SelectorSequential SelectorMode = "sequential"
	SelectorRandom     SelectorMode = "random"
	SelectorBalance    SelectorMode = "balance"
)

type Config struct {
	Mode             ExecutionMode          `yaml:"mode"`
	LogLevel         string                 `yaml:"log_level"`
	UnifiedAPI       UnifiedAPIConfig       `yaml:"unified_api"`
	ControlPlane     ControlPlaneConfig     `yaml:"control_plane"`
	Strategy         StrategyConfig         `yaml:"strategy"`
	ErrorAttribution ErrorAttributionConfig `yaml:"error_attribution"`
	Tracing          TracingConfig          `yaml:"tracing"`
	Persistence      PersistenceConfig      `yaml:"persistence"`
	Services         []ServiceConfig        `yaml:"services"`
}

type UnifiedAPIConfig struct {
	Listen   string `yaml:"listen"`
	Password string `yaml:"password"`
}

type ControlPlaneConfig struct {
	Enabled          bool          `yaml:"enabled"`
	ReadToken        string        `yaml:"read_token"`
	MutateToken      string        `yaml:"mutate_token"`
	RequireActor     bool          `yaml:"require_actor"`
	LocalhostOnly    bool          `yaml:"localhost_only"`
	Allowlist        []string      `yaml:"allowlist"`
	TokenGracePeriod time.Duration `yaml:"token_grace_period"`
}

type StrategyConfig struct {
	Mode                         SelectorMode                     `yaml:"mode"`
	FailureThreshold             int                              `yaml:"failure_threshold"`
	CooldownDuration             time.Duration                    `yaml:"cooldown_duration"`
	FallbackOnRetryableErrors    bool                             `yaml:"fallback_on_retryable_errors"`
	MaxFallbackAttempts          int                              `yaml:"max_fallback_attempts"`
	RetryableCategories          []string                         `yaml:"retryable_categories"`
	PreferredServicesByOperation map[string][]string              `yaml:"preferred_services_by_operation"`
	OperationPolicies            map[string]OperationPolicyConfig `yaml:"operation_policies"`
}

type OperationPolicyConfig struct {
	FallbackMode        string   `json:"fallback_mode,omitempty" yaml:"fallback_mode,omitempty"`
	MaxFallbackAttempts int      `json:"max_fallback_attempts,omitempty" yaml:"max_fallback_attempts,omitempty"`
	RetryableCategories []string `json:"retryable_categories,omitempty" yaml:"retryable_categories,omitempty"`
}

type ErrorAttributionConfig struct {
	Enabled bool `yaml:"enabled"`
}

type TracingConfig struct {
	Enabled      bool `yaml:"enabled"`
	HistoryLimit int  `yaml:"history_limit"`
}

type PersistenceConfig struct {
	Enabled               bool   `yaml:"enabled"`
	RuntimeStatePath      string `yaml:"runtime_state_path"`
	ControlPlaneStatePath string `yaml:"control_plane_state_path"`
	AuditLogPath          string `yaml:"audit_log_path"`
	SnapshotDir           string `yaml:"snapshot_dir"`
	AuditHistoryLimit     int    `yaml:"audit_history_limit"`
	SnapshotLimit         int    `yaml:"snapshot_limit"`
}

type ServiceConfig struct {
	Name                string   `yaml:"name"`
	Language            string   `yaml:"language"`
	Endpoint            string   `yaml:"endpoint"`
	Enabled             bool     `yaml:"enabled"`
	SupportedOperations []string `yaml:"supported_operations"`
}

func Load(path string) (Config, error) {
	cfg := DefaultConfig()
	normalizedPath := strings.TrimSpace(path)
	if normalizedPath == "" {
		cfg.Normalize()
		return cfg, nil
	}

	data, err := os.ReadFile(normalizedPath)
	if err != nil {
		return Config{}, fmt.Errorf("read config %s: %w", normalizedPath, err)
	}
	if len(bytes.TrimSpace(data)) == 0 {
		cfg.Normalize()
		return cfg, nil
	}
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return Config{}, fmt.Errorf("parse config %s: %w", normalizedPath, err)
	}
	cfg.Normalize()
	return cfg, nil
}

func DefaultConfig() Config {
	return Config{
		Mode:     ModeStrategy,
		LogLevel: "info",
		UnifiedAPI: UnifiedAPIConfig{
			Listen:   "0.0.0.0:9788",
			Password: "123456",
		},
		ControlPlane: ControlPlaneConfig{
			Enabled:          true,
			ReadToken:        "123456",
			MutateToken:      "123456",
			RequireActor:     true,
			LocalhostOnly:    false,
			Allowlist:        nil,
			TokenGracePeriod: 15 * time.Minute,
		},
		Strategy: StrategyConfig{
			Mode:                      SelectorBalance,
			FailureThreshold:          10,
			CooldownDuration:          30 * time.Minute,
			FallbackOnRetryableErrors: true,
			MaxFallbackAttempts:       5,
			RetryableCategories: []string{
				"transport_error",
				"delegation_error",
				"timeout_error",
				"service_unavailable",
				"unsupported_operation",
			},
			PreferredServicesByOperation: map[string][]string{
				"protocol.echo":              {"GolangProtocol", "JSProtocol", "PythonProtocol", "RustProtocol"},
				"protocol.query.encode":      {"JSProtocol", "GolangProtocol"},
				"protocol.regex.extract":     {"PythonProtocol", "JSProtocol"},
				"protocol.hash.sha256":       {"RustProtocol", "GolangProtocol"},
				"protocol.template.render":   {"JSProtocol"},
				"protocol.data.flatten":      {"PythonProtocol"},
				"protocol.headers.normalize": {"GolangProtocol"},
				"protocol.bytes.hex":         {"RustProtocol"},
				"protocol.bytes.xor":         {"RustProtocol"},
				"protocol.text.slugify":      {"PythonProtocol"},
				"protocol.json.compact":      {"JSProtocol"},
			},
			OperationPolicies: map[string]OperationPolicyConfig{
				"protocol.query.encode": {
					FallbackMode:        "enabled",
					MaxFallbackAttempts: 2,
				},
				"protocol.regex.extract": {
					FallbackMode:        "enabled",
					MaxFallbackAttempts: 2,
				},
				"protocol.hash.sha256": {
					FallbackMode:        "enabled",
					MaxFallbackAttempts: 2,
				},
				"protocol.data.flatten": {
					FallbackMode: "disabled",
				},
				"codex.semantic.step": {
					FallbackMode:        "enabled",
					MaxFallbackAttempts: 12,
					RetryableCategories: []string{
						"transport_error",
						"delegation_error",
						"timeout_error",
						"service_unavailable",
						"unsupported_operation",
					},
				},
			},
		},
		ErrorAttribution: ErrorAttributionConfig{
			Enabled: true,
		},
		Tracing: TracingConfig{
			Enabled:      true,
			HistoryLimit: 200,
		},
		Persistence: PersistenceConfig{
			Enabled:               true,
			RuntimeStatePath:      "state/runtime-overrides.json",
			ControlPlaneStatePath: "state/control-plane-state.json",
			AuditLogPath:          "state/audit-log.jsonl",
			SnapshotDir:           "state/runtime-snapshots",
			AuditHistoryLimit:     500,
			SnapshotLimit:         200,
		},
		Services: []ServiceConfig{
			{Name: "GolangProtocol", Language: "go", Endpoint: "http://127.0.0.1:11001", Enabled: true, SupportedOperations: []string{"health.inspect", "protocol.echo", "protocol.headers.normalize", "protocol.query.encode", "protocol.hash.sha256"}},
			{Name: "JSProtocol", Language: "javascript", Endpoint: "http://127.0.0.1:11002", Enabled: true, SupportedOperations: []string{"health.inspect", "protocol.echo", "protocol.template.render", "protocol.json.compact", "protocol.query.encode", "protocol.regex.extract"}},
			{Name: "PythonProtocol-01", Language: "python", Endpoint: "http://python-protocol-service-01:9100", Enabled: true, SupportedOperations: []string{"health.inspect", "protocol.echo", "protocol.regex.extract", "protocol.text.slugify", "protocol.data.flatten", "codex.semantic.step"}},
			{Name: "PythonProtocol-02", Language: "python", Endpoint: "http://python-protocol-service-02:9100", Enabled: true, SupportedOperations: []string{"health.inspect", "protocol.echo", "protocol.regex.extract", "protocol.text.slugify", "protocol.data.flatten", "codex.semantic.step"}},
			{Name: "PythonProtocol-03", Language: "python", Endpoint: "http://python-protocol-service-03:9100", Enabled: true, SupportedOperations: []string{"health.inspect", "protocol.echo", "protocol.regex.extract", "protocol.text.slugify", "protocol.data.flatten", "codex.semantic.step"}},
			{Name: "PythonProtocol-04", Language: "python", Endpoint: "http://python-protocol-service-04:9100", Enabled: true, SupportedOperations: []string{"health.inspect", "protocol.echo", "protocol.regex.extract", "protocol.text.slugify", "protocol.data.flatten", "codex.semantic.step"}},
			{Name: "PythonProtocol-05", Language: "python", Endpoint: "http://python-protocol-service-05:9100", Enabled: true, SupportedOperations: []string{"health.inspect", "protocol.echo", "protocol.regex.extract", "protocol.text.slugify", "protocol.data.flatten", "codex.semantic.step"}},
			{Name: "PythonProtocol-06", Language: "python", Endpoint: "http://python-protocol-service-06:9100", Enabled: true, SupportedOperations: []string{"health.inspect", "protocol.echo", "protocol.regex.extract", "protocol.text.slugify", "protocol.data.flatten", "codex.semantic.step"}},
			{Name: "PythonProtocol-07", Language: "python", Endpoint: "http://python-protocol-service-07:9100", Enabled: true, SupportedOperations: []string{"health.inspect", "protocol.echo", "protocol.regex.extract", "protocol.text.slugify", "protocol.data.flatten", "codex.semantic.step"}},
			{Name: "PythonProtocol-08", Language: "python", Endpoint: "http://python-protocol-service-08:9100", Enabled: true, SupportedOperations: []string{"health.inspect", "protocol.echo", "protocol.regex.extract", "protocol.text.slugify", "protocol.data.flatten", "codex.semantic.step"}},
			{Name: "PythonProtocol-09", Language: "python", Endpoint: "http://python-protocol-service-09:9100", Enabled: true, SupportedOperations: []string{"health.inspect", "protocol.echo", "protocol.regex.extract", "protocol.text.slugify", "protocol.data.flatten", "codex.semantic.step"}},
			{Name: "PythonProtocol-10", Language: "python", Endpoint: "http://python-protocol-service-10:9100", Enabled: true, SupportedOperations: []string{"health.inspect", "protocol.echo", "protocol.regex.extract", "protocol.text.slugify", "protocol.data.flatten", "codex.semantic.step"}},
			{Name: "PythonProtocol-11", Language: "python", Endpoint: "http://python-protocol-service-11:9100", Enabled: true, SupportedOperations: []string{"health.inspect", "protocol.echo", "protocol.regex.extract", "protocol.text.slugify", "protocol.data.flatten", "codex.semantic.step"}},
			{Name: "PythonProtocol-12", Language: "python", Endpoint: "http://python-protocol-service-12:9100", Enabled: true, SupportedOperations: []string{"health.inspect", "protocol.echo", "protocol.regex.extract", "protocol.text.slugify", "protocol.data.flatten", "codex.semantic.step"}},
			{Name: "RustProtocol", Language: "rust", Endpoint: "http://127.0.0.1:11004", Enabled: true, SupportedOperations: []string{"health.inspect", "protocol.echo", "protocol.hash.sha256", "protocol.bytes.hex", "protocol.bytes.xor"}},
		},
	}
}

func (c *Config) Normalize() {
	if c.Mode != ModeSpecified {
		c.Mode = ModeStrategy
	}
	if c.LogLevel == "" {
		c.LogLevel = "info"
	}
	if c.UnifiedAPI.Listen == "" {
		c.UnifiedAPI.Listen = "0.0.0.0:9788"
	}
	if c.ControlPlane.ReadToken == "" {
		if strings.TrimSpace(c.UnifiedAPI.Password) != "" {
			c.ControlPlane.ReadToken = strings.TrimSpace(c.UnifiedAPI.Password)
		} else {
			c.ControlPlane.ReadToken = "123456"
		}
	}
	if c.ControlPlane.MutateToken == "" {
		c.ControlPlane.MutateToken = c.ControlPlane.ReadToken
	}
	c.ControlPlane.Allowlist = normalizeCategories(c.ControlPlane.Allowlist)
	if c.ControlPlane.TokenGracePeriod <= 0 {
		c.ControlPlane.TokenGracePeriod = 15 * time.Minute
	}
	if c.Strategy.Mode != SelectorRandom && c.Strategy.Mode != SelectorBalance {
		c.Strategy.Mode = SelectorSequential
	}
	if c.Strategy.FailureThreshold <= 0 {
		c.Strategy.FailureThreshold = 3
	}
	if c.Strategy.CooldownDuration <= 0 {
		c.Strategy.CooldownDuration = 24 * time.Hour
	}
	if c.Strategy.MaxFallbackAttempts <= 0 {
		c.Strategy.MaxFallbackAttempts = 3
	}
	c.Strategy.RetryableCategories = normalizeCategories(c.Strategy.RetryableCategories)
	c.Strategy.OperationPolicies = normalizeOperationPolicies(c.Strategy.OperationPolicies)
	if !c.Tracing.Enabled && c.Tracing.HistoryLimit == 0 {
		c.Tracing.Enabled = true
	}
	if c.Tracing.HistoryLimit <= 0 {
		c.Tracing.HistoryLimit = 200
	}
	if !c.Persistence.Enabled && c.Persistence.RuntimeStatePath == "" && c.Persistence.AuditLogPath == "" {
		c.Persistence.Enabled = true
	}
	if c.Persistence.RuntimeStatePath == "" {
		c.Persistence.RuntimeStatePath = "state/runtime-overrides.json"
	}
	if c.Persistence.ControlPlaneStatePath == "" {
		c.Persistence.ControlPlaneStatePath = "state/control-plane-state.json"
	}
	if c.Persistence.AuditLogPath == "" {
		c.Persistence.AuditLogPath = "state/audit-log.jsonl"
	}
	if c.Persistence.SnapshotDir == "" {
		c.Persistence.SnapshotDir = "state/runtime-snapshots"
	}
	if c.Persistence.AuditHistoryLimit <= 0 {
		c.Persistence.AuditHistoryLimit = 500
	}
	if c.Persistence.SnapshotLimit <= 0 {
		c.Persistence.SnapshotLimit = 200
	}
	if c.Strategy.PreferredServicesByOperation == nil {
		c.Strategy.PreferredServicesByOperation = map[string][]string{}
		return
	}
	normalizedPreferences := make(map[string][]string, len(c.Strategy.PreferredServicesByOperation))
	for operation, services := range c.Strategy.PreferredServicesByOperation {
		normalizedOperation := strings.TrimSpace(operation)
		if normalizedOperation == "" {
			continue
		}
		seen := make(map[string]struct{}, len(services))
		ordered := make([]string, 0, len(services))
		for _, service := range services {
			normalizedService := strings.TrimSpace(service)
			if normalizedService == "" {
				continue
			}
			if _, ok := seen[normalizedService]; ok {
				continue
			}
			seen[normalizedService] = struct{}{}
			ordered = append(ordered, normalizedService)
		}
		normalizedPreferences[normalizedOperation] = ordered
	}
	c.Strategy.PreferredServicesByOperation = normalizedPreferences
}

func normalizeCategories(categories []string) []string {
	seen := make(map[string]struct{}, len(categories))
	normalized := make([]string, 0, len(categories))
	for _, category := range categories {
		item := strings.TrimSpace(category)
		if item == "" {
			continue
		}
		if _, ok := seen[item]; ok {
			continue
		}
		seen[item] = struct{}{}
		normalized = append(normalized, item)
	}
	return normalized
}

func normalizeOperationPolicies(policies map[string]OperationPolicyConfig) map[string]OperationPolicyConfig {
	if policies == nil {
		return map[string]OperationPolicyConfig{}
	}
	normalized := make(map[string]OperationPolicyConfig, len(policies))
	for operation, policy := range policies {
		normalizedOperation := strings.TrimSpace(operation)
		if normalizedOperation == "" {
			continue
		}
		policy.FallbackMode = strings.ToLower(strings.TrimSpace(policy.FallbackMode))
		policy.RetryableCategories = normalizeCategories(policy.RetryableCategories)
		normalized[normalizedOperation] = policy
	}
	return normalized
}
