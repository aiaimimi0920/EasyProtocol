package attribution

import "sync"

type Manager struct {
	mu      sync.Mutex
	limit   int
	records []Record
}

func NewManager(limit int) *Manager {
	if limit <= 0 {
		limit = 100
	}
	return &Manager{limit: limit}
}

func (m *Manager) Record(record Record) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.records = append([]Record{record}, m.records...)
	if len(m.records) > m.limit {
		m.records = m.records[:m.limit]
	}
}

func (m *Manager) List() []Record {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]Record, len(m.records))
	copy(out, m.records)
	return out
}

func (m *Manager) Clear() {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.records = nil
}
