package services

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

type AuditRecord struct {
	ID         string         `json:"id"`
	Action     string         `json:"action"`
	TargetType string         `json:"target_type"`
	Target     string         `json:"target,omitempty"`
	Actor      string         `json:"actor,omitempty"`
	Reason     string         `json:"reason,omitempty"`
	Details    map[string]any `json:"details,omitempty"`
	CreatedAt  time.Time      `json:"created_at"`
}

type AuditFilter struct {
	Action     string
	TargetType string
	Target     string
	Limit      int
}

type AuditRetentionSummary struct {
	Enabled        bool      `json:"enabled"`
	Path           string    `json:"path,omitempty"`
	Limit          int       `json:"limit"`
	RecordCount    int       `json:"record_count"`
	FileExists     bool      `json:"file_exists"`
	FileSizeBytes  int64     `json:"file_size_bytes"`
	NewestRecordAt time.Time `json:"newest_record_at,omitempty"`
	OldestRecordAt time.Time `json:"oldest_record_at,omitempty"`
}

type AuditPruneSummary struct {
	RequestedKeep  int       `json:"requested_keep"`
	EffectiveKeep  int       `json:"effective_keep"`
	BeforeCount    int       `json:"before_count"`
	AfterCount     int       `json:"after_count"`
	PrunedCount    int       `json:"pruned_count"`
	NewestRecordAt time.Time `json:"newest_record_at,omitempty"`
	OldestRecordAt time.Time `json:"oldest_record_at,omitempty"`
	FileSizeBytes  int64     `json:"file_size_bytes"`
}

type AuditStore struct {
	mu      sync.Mutex
	enabled bool
	path    string
	limit   int
	records []AuditRecord
}

func NewAuditStore(enabled bool, path string, limit int) *AuditStore {
	if limit <= 0 {
		limit = 500
	}
	return &AuditStore{
		enabled: enabled,
		path:    path,
		limit:   limit,
	}
}

func (s *AuditStore) Enabled() bool {
	if s == nil {
		return false
	}
	return s.enabled
}

func (s *AuditStore) Load() error {
	if s == nil || !s.enabled || s.path == "" {
		return nil
	}
	file, err := os.Open(s.path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	records := make([]AuditRecord, 0)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var record AuditRecord
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			return err
		}
		records = append(records, record)
	}
	if err := scanner.Err(); err != nil {
		return err
	}
	if len(records) > s.limit {
		records = records[len(records)-s.limit:]
	}
	// newest first in memory
	for left, right := 0, len(records)-1; left < right; left, right = left+1, right-1 {
		records[left], records[right] = records[right], records[left]
	}
	s.mu.Lock()
	s.records = records
	s.mu.Unlock()
	return nil
}

func (s *AuditStore) Record(record AuditRecord) error {
	if s == nil || !s.enabled {
		return nil
	}
	record.ID = fmt.Sprintf("audit-%d", time.Now().UnixNano())
	record.CreatedAt = time.Now().UTC()
	if record.Details == nil {
		record.Details = map[string]any{}
	}
	data, err := json.Marshal(record)
	if err != nil {
		return err
	}
	if s.path != "" {
		if err := os.MkdirAll(filepath.Dir(s.path), 0o755); err != nil {
			return err
		}
		file, err := os.OpenFile(s.path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
		if err != nil {
			return err
		}
		if _, err := file.Write(append(data, '\n')); err != nil {
			file.Close()
			return err
		}
		if err := file.Close(); err != nil {
			return err
		}
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.records = append([]AuditRecord{record}, s.records...)
	if len(s.records) > s.limit {
		s.records = s.records[:s.limit]
	}
	return nil
}

func (s *AuditStore) RetentionSummary() AuditRetentionSummary {
	if s == nil {
		return AuditRetentionSummary{}
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.retentionSummaryLocked()
}

func (s *AuditStore) retentionSummaryLocked() AuditRetentionSummary {
	summary := AuditRetentionSummary{
		Enabled:     s.enabled,
		Path:        s.path,
		Limit:       s.limit,
		RecordCount: len(s.records),
	}
	if len(s.records) > 0 {
		summary.NewestRecordAt = s.records[0].CreatedAt
		summary.OldestRecordAt = s.records[len(s.records)-1].CreatedAt
	}
	if s.path != "" {
		if info, err := os.Stat(s.path); err == nil {
			summary.FileExists = true
			summary.FileSizeBytes = info.Size()
		}
	}
	return summary
}

func (s *AuditStore) Prune(keep int) (AuditPruneSummary, error) {
	if s == nil {
		return AuditPruneSummary{}, nil
	}
	if keep < 0 {
		keep = 0
	}
	s.mu.Lock()
	beforeCount := len(s.records)
	effectiveKeep := keep
	if effectiveKeep > beforeCount {
		effectiveKeep = beforeCount
	}
	if effectiveKeep < beforeCount {
		s.records = append([]AuditRecord(nil), s.records[:effectiveKeep]...)
	}
	summary := AuditPruneSummary{
		RequestedKeep: keep,
		EffectiveKeep: effectiveKeep,
		BeforeCount:   beforeCount,
		AfterCount:    len(s.records),
		PrunedCount:   beforeCount - len(s.records),
	}
	if len(s.records) > 0 {
		summary.NewestRecordAt = s.records[0].CreatedAt
		summary.OldestRecordAt = s.records[len(s.records)-1].CreatedAt
	}
	recordsToPersist := append([]AuditRecord(nil), s.records...)
	path := s.path
	enabled := s.enabled
	s.mu.Unlock()

	if enabled && path != "" {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			return AuditPruneSummary{}, err
		}
		lines := make([][]byte, 0, len(recordsToPersist))
		for idx := len(recordsToPersist) - 1; idx >= 0; idx-- {
			data, err := json.Marshal(recordsToPersist[idx])
			if err != nil {
				return AuditPruneSummary{}, err
			}
			lines = append(lines, data)
		}
		payload := []byte{}
		for _, line := range lines {
			payload = append(payload, line...)
			payload = append(payload, '\n')
		}
		tempPath := path + ".tmp"
		if err := os.WriteFile(tempPath, payload, 0o644); err != nil {
			return AuditPruneSummary{}, err
		}
		if err := os.Rename(tempPath, path); err != nil {
			return AuditPruneSummary{}, err
		}
		if info, err := os.Stat(path); err == nil {
			summary.FileSizeBytes = info.Size()
		}
	}
	return summary, nil
}

func (s *AuditStore) List(filter AuditFilter) []AuditRecord {
	if s == nil {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]AuditRecord, 0, len(s.records))
	for _, record := range s.records {
		if item := strings.TrimSpace(filter.Action); item != "" && record.Action != item {
			continue
		}
		if item := strings.TrimSpace(filter.TargetType); item != "" && record.TargetType != item {
			continue
		}
		if item := strings.TrimSpace(filter.Target); item != "" && record.Target != item {
			continue
		}
		out = append(out, record)
		if filter.Limit > 0 && len(out) >= filter.Limit {
			break
		}
	}
	return out
}

func ParseAuditLimit(raw string) int {
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" {
		return 0
	}
	value, err := strconv.Atoi(trimmed)
	if err != nil || value < 0 {
		return 0
	}
	return value
}
