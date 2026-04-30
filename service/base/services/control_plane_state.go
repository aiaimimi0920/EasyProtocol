package services

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"
)

type ControlPlaneState struct {
	UpdatedAt              time.Time                     `json:"updated_at"`
	ReadToken              string                        `json:"read_token,omitempty"`
	MutateToken            string                        `json:"mutate_token,omitempty"`
	PreviousReadToken      string                        `json:"previous_read_token,omitempty"`
	PreviousMutateToken    string                        `json:"previous_mutate_token,omitempty"`
	ReadTokenGraceUntil    time.Time                     `json:"read_token_grace_until,omitempty"`
	MutateTokenGraceUntil  time.Time                     `json:"mutate_token_grace_until,omitempty"`
	FreezeEnabled          bool                          `json:"freeze_enabled"`
	MaintenanceEnabled     bool                          `json:"maintenance_enabled"`
	MaintenanceReason      string                        `json:"maintenance_reason,omitempty"`
	MaintenanceETA         time.Time                     `json:"maintenance_eta,omitempty"`
	MaintenanceStartedAt   time.Time                     `json:"maintenance_started_at,omitempty"`
	LastMaintenanceSummary *MaintenanceCompletionSummary `json:"last_maintenance_summary,omitempty"`
}

type MaintenanceCompletionSummary struct {
	StartedAt             time.Time                         `json:"started_at,omitempty"`
	CompletedAt           time.Time                         `json:"completed_at,omitempty"`
	DurationSeconds       int64                             `json:"duration_seconds"`
	Reason                string                            `json:"reason,omitempty"`
	ETA                   time.Time                         `json:"eta,omitempty"`
	PublicRejectCount     int                               `json:"public_reject_count"`
	TopRejectedOperations []MaintenanceOperationRejectCount `json:"top_rejected_operations,omitempty"`
}

type MaintenanceOperationRejectCount struct {
	Operation string `json:"operation"`
	Count     int    `json:"count"`
}

func (s ControlPlaneState) IsZero() bool {
	return s.UpdatedAt.IsZero() &&
		s.ReadToken == "" &&
		s.MutateToken == "" &&
		s.PreviousReadToken == "" &&
		s.PreviousMutateToken == "" &&
		s.ReadTokenGraceUntil.IsZero() &&
		s.MutateTokenGraceUntil.IsZero() &&
		!s.FreezeEnabled &&
		!s.MaintenanceEnabled &&
		s.MaintenanceReason == "" &&
		s.MaintenanceETA.IsZero() &&
		s.MaintenanceStartedAt.IsZero() &&
		s.LastMaintenanceSummary == nil
}

type ControlPlaneStateStore struct {
	enabled bool
	path    string
}

func NewControlPlaneStateStore(enabled bool, path string) *ControlPlaneStateStore {
	return &ControlPlaneStateStore{
		enabled: enabled,
		path:    path,
	}
}

func (s *ControlPlaneStateStore) Enabled() bool {
	if s == nil {
		return false
	}
	return s.enabled
}

func (s *ControlPlaneStateStore) Load() (ControlPlaneState, error) {
	if s == nil || !s.enabled || s.path == "" {
		return ControlPlaneState{}, nil
	}
	data, err := os.ReadFile(s.path)
	if err != nil {
		if os.IsNotExist(err) {
			return ControlPlaneState{}, nil
		}
		return ControlPlaneState{}, err
	}
	var state ControlPlaneState
	if err := json.Unmarshal(data, &state); err != nil {
		return ControlPlaneState{}, err
	}
	return state, nil
}

func (s *ControlPlaneStateStore) Save(state ControlPlaneState) error {
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
