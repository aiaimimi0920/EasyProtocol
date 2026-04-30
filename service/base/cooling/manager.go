package cooling

import (
	"sync"
	"time"
)

type State struct {
	Service           string
	FailureCount      int
	Cooled            bool
	CooledUntil       time.Time
	LastErrorCategory string
}

type Manager struct {
	threshold int
	duration  time.Duration
	mu        sync.Mutex
	states    map[string]State
}

func New(threshold int, duration time.Duration) *Manager {
	if threshold <= 0 {
		threshold = 3
	}
	if duration <= 0 {
		duration = 24 * time.Hour
	}
	return &Manager{
		threshold: threshold,
		duration:  duration,
		states:    make(map[string]State),
	}
}

func (m *Manager) currentLocked(service string, now time.Time) State {
	state := m.states[service]
	state.Service = service
	if state.Cooled && !state.CooledUntil.IsZero() && now.After(state.CooledUntil) {
		state.Cooled = false
		state.CooledUntil = time.Time{}
	}
	return state
}

func (m *Manager) Snapshot(service string, now time.Time) State {
	m.mu.Lock()
	defer m.mu.Unlock()
	state := m.currentLocked(service, now)
	m.states[service] = state
	return state
}

func (m *Manager) IsCooled(service string, now time.Time) bool {
	state := m.Snapshot(service, now)
	return state.Cooled
}

func (m *Manager) RecordFailure(service, category string, countsTowardCooling bool, now time.Time) State {
	m.mu.Lock()
	defer m.mu.Unlock()

	state := m.currentLocked(service, now)
	state.LastErrorCategory = category

	if countsTowardCooling {
		state.FailureCount++
		if state.FailureCount >= m.threshold {
			state.FailureCount = 0
			state.Cooled = true
			state.CooledUntil = now.Add(m.duration)
		}
	}

	m.states[service] = state
	return state
}

func (m *Manager) RecordSuccess(service string, now time.Time) State {
	m.mu.Lock()
	defer m.mu.Unlock()
	state := m.currentLocked(service, now)
	state.FailureCount = 0
	m.states[service] = state
	return state
}

func (m *Manager) Reset(service string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.states, service)
}

func (m *Manager) ResetAll() {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.states = make(map[string]State)
}

func (m *Manager) All(now time.Time) []State {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]State, 0, len(m.states))
	for service := range m.states {
		state := m.currentLocked(service, now)
		m.states[service] = state
		out = append(out, state)
	}
	return out
}
