package services

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

type RuntimeSnapshot struct {
	ID        string       `json:"id"`
	Action    string       `json:"action"`
	Actor     string       `json:"actor,omitempty"`
	Reason    string       `json:"reason,omitempty"`
	CreatedAt time.Time    `json:"created_at"`
	State     RuntimeState `json:"state"`
}

type RuntimeSnapshotSummary struct {
	ID        string    `json:"id"`
	Action    string    `json:"action"`
	Actor     string    `json:"actor,omitempty"`
	Reason    string    `json:"reason,omitempty"`
	CreatedAt time.Time `json:"created_at"`
}

type RuntimeSnapshotStore struct {
	mu      sync.Mutex
	enabled bool
	dir     string
	limit   int
	items   []RuntimeSnapshotSummary
}

func NewRuntimeSnapshotStore(enabled bool, dir string, limit int) *RuntimeSnapshotStore {
	if limit <= 0 {
		limit = 200
	}
	return &RuntimeSnapshotStore{
		enabled: enabled,
		dir:     dir,
		limit:   limit,
	}
}

func (s *RuntimeSnapshotStore) Enabled() bool {
	if s == nil {
		return false
	}
	return s.enabled
}

func (s *RuntimeSnapshotStore) Load() error {
	if s == nil || !s.enabled || s.dir == "" {
		return nil
	}
	entries, err := os.ReadDir(s.dir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	items := make([]RuntimeSnapshotSummary, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		data, err := os.ReadFile(filepath.Join(s.dir, entry.Name()))
		if err != nil {
			return err
		}
		var snapshot RuntimeSnapshot
		if err := json.Unmarshal(data, &snapshot); err != nil {
			return err
		}
		items = append(items, snapshot.summary())
	}
	sort.Slice(items, func(i, j int) bool {
		return items[i].CreatedAt.After(items[j].CreatedAt)
	})
	if len(items) > s.limit {
		items = items[:s.limit]
	}
	s.mu.Lock()
	s.items = items
	s.mu.Unlock()
	return nil
}

func (s *RuntimeSnapshotStore) Save(action, actor, reason string, state RuntimeState) (RuntimeSnapshotSummary, error) {
	if s == nil || !s.enabled || s.dir == "" {
		return RuntimeSnapshotSummary{}, nil
	}
	snapshot := RuntimeSnapshot{
		ID:        fmt.Sprintf("snapshot-%d", time.Now().UnixNano()),
		Action:    action,
		Actor:     actor,
		Reason:    reason,
		CreatedAt: time.Now().UTC(),
		State:     state,
	}
	if err := os.MkdirAll(s.dir, 0o755); err != nil {
		return RuntimeSnapshotSummary{}, err
	}
	data, err := json.MarshalIndent(snapshot, "", "  ")
	if err != nil {
		return RuntimeSnapshotSummary{}, err
	}
	path := filepath.Join(s.dir, snapshot.ID+".json")
	if err := os.WriteFile(path, data, 0o644); err != nil {
		return RuntimeSnapshotSummary{}, err
	}
	summary := snapshot.summary()
	s.mu.Lock()
	s.items = append([]RuntimeSnapshotSummary{summary}, s.items...)
	if len(s.items) > s.limit {
		for _, old := range s.items[s.limit:] {
			_ = os.Remove(filepath.Join(s.dir, old.ID+".json"))
		}
		s.items = s.items[:s.limit]
	}
	s.mu.Unlock()
	return summary, nil
}

func (s *RuntimeSnapshotStore) List(limit int) []RuntimeSnapshotSummary {
	if s == nil {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	out := append([]RuntimeSnapshotSummary(nil), s.items...)
	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out
}

func (s *RuntimeSnapshotStore) LoadSnapshot(id string) (RuntimeSnapshot, error) {
	if s == nil || !s.enabled || s.dir == "" {
		return RuntimeSnapshot{}, os.ErrNotExist
	}
	data, err := os.ReadFile(filepath.Join(s.dir, strings.TrimSpace(id)+".json"))
	if err != nil {
		return RuntimeSnapshot{}, err
	}
	var snapshot RuntimeSnapshot
	if err := json.Unmarshal(data, &snapshot); err != nil {
		return RuntimeSnapshot{}, err
	}
	return snapshot, nil
}

func (s RuntimeSnapshot) Summary() RuntimeSnapshotSummary {
	return s.summary()
}

func (s RuntimeSnapshot) summary() RuntimeSnapshotSummary {
	return RuntimeSnapshotSummary{
		ID:        s.ID,
		Action:    s.Action,
		Actor:     s.Actor,
		Reason:    s.Reason,
		CreatedAt: s.CreatedAt,
	}
}
