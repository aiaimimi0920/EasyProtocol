package services

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"

	"easy_protocol/config"
)

type RuntimeState struct {
	UpdatedAt time.Time                      `json:"updated_at"`
	Policy    RuntimePolicyState             `json:"policy"`
	Services  map[string]RuntimeServiceState `json:"services,omitempty"`
}

func (s RuntimeState) IsZero() bool {
	return s.UpdatedAt.IsZero() &&
		!s.Policy.Global.FallbackOnRetryableErrors &&
		s.Policy.Global.MaxFallbackAttempts == 0 &&
		len(s.Policy.Global.RetryableCategories) == 0 &&
		len(s.Policy.Operations) == 0 &&
		len(s.Services) == 0
}

type RuntimePolicyState struct {
	Global     RuntimeGlobalPolicyState               `json:"global"`
	Operations map[string]RuntimeOperationPolicyState `json:"operations,omitempty"`
}

type RuntimeGlobalPolicyState struct {
	FallbackOnRetryableErrors bool     `json:"fallback_on_retryable_errors"`
	MaxFallbackAttempts       int      `json:"max_fallback_attempts"`
	RetryableCategories       []string `json:"retryable_categories,omitempty"`
}

type RuntimeOperationPolicyState struct {
	PreferredServices []string                     `json:"preferred_services,omitempty"`
	Policy            config.OperationPolicyConfig `json:"policy,omitempty"`
}

type RuntimeServiceState struct {
	Enabled bool `json:"enabled"`
}

type RuntimeStateStore struct {
	enabled bool
	path    string
}

func NewRuntimeStateStore(enabled bool, path string) *RuntimeStateStore {
	return &RuntimeStateStore{
		enabled: enabled,
		path:    path,
	}
}

func (s *RuntimeStateStore) Enabled() bool {
	if s == nil {
		return false
	}
	return s.enabled
}

func (s *RuntimeStateStore) Load() (RuntimeState, error) {
	if s == nil || !s.enabled || s.path == "" {
		return RuntimeState{}, nil
	}
	data, err := os.ReadFile(s.path)
	if err != nil {
		if os.IsNotExist(err) {
			return RuntimeState{}, nil
		}
		return RuntimeState{}, err
	}
	var state RuntimeState
	if err := json.Unmarshal(data, &state); err != nil {
		return RuntimeState{}, err
	}
	return state, nil
}

func (s *RuntimeStateStore) Save(state RuntimeState) error {
	if s == nil || !s.enabled || s.path == "" {
		return nil
	}
	state.UpdatedAt = time.Now().UTC()
	if err := os.MkdirAll(filepath.Dir(s.path), 0o755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return err
	}
	tempPath := s.path + ".tmp"
	if err := os.WriteFile(tempPath, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tempPath, s.path)
}
