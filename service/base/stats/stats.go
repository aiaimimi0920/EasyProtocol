package stats

import (
	"sort"
	"sync"
)

type ServiceStats struct {
	Service           string `json:"service"`
	SuccessCount      int64  `json:"success_count"`
	FailureCount      int64  `json:"failure_count"`
	CooldownCount     int64  `json:"cooldown_count"`
	ActiveRequests    int64  `json:"active_requests"`
	LastErrorCategory string `json:"last_error_category,omitempty"`
}

type OperationStats struct {
	Operation           string `json:"operation"`
	SuccessCount        int64  `json:"success_count"`
	FailureCount        int64  `json:"failure_count"`
	ActiveRequests      int64  `json:"active_requests"`
	LastSelectedService string `json:"last_selected_service,omitempty"`
	LastErrorCategory   string `json:"last_error_category,omitempty"`
}

type ServiceOperationStats struct {
	Service           string `json:"service"`
	Operation         string `json:"operation"`
	SuccessCount      int64  `json:"success_count"`
	FailureCount      int64  `json:"failure_count"`
	CooldownCount     int64  `json:"cooldown_count"`
	ActiveRequests    int64  `json:"active_requests"`
	LastErrorCategory string `json:"last_error_category,omitempty"`
}

type Manager struct {
	mu                   sync.Mutex
	entries              map[string]*ServiceStats
	operationEntries     map[string]*OperationStats
	serviceOperationRuns map[string]map[string]*ServiceOperationStats
}

func New() *Manager {
	return &Manager{
		entries:              make(map[string]*ServiceStats),
		operationEntries:     make(map[string]*OperationStats),
		serviceOperationRuns: make(map[string]map[string]*ServiceOperationStats),
	}
}

func (m *Manager) ensure(service string) *ServiceStats {
	entry, ok := m.entries[service]
	if ok {
		return entry
	}
	entry = &ServiceStats{Service: service}
	m.entries[service] = entry
	return entry
}

func (m *Manager) ensureOperation(operation string) *OperationStats {
	entry, ok := m.operationEntries[operation]
	if ok {
		return entry
	}
	entry = &OperationStats{Operation: operation}
	m.operationEntries[operation] = entry
	return entry
}

func (m *Manager) ensureServiceOperation(service, operation string) *ServiceOperationStats {
	byService, ok := m.serviceOperationRuns[service]
	if !ok {
		byService = make(map[string]*ServiceOperationStats)
		m.serviceOperationRuns[service] = byService
	}
	entry, ok := byService[operation]
	if ok {
		return entry
	}
	entry = &ServiceOperationStats{
		Service:   service,
		Operation: operation,
	}
	byService[operation] = entry
	return entry
}

func (m *Manager) Begin(service, operation string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	entry := m.ensure(service)
	entry.ActiveRequests++
	if operation != "" {
		opEntry := m.ensureOperation(operation)
		opEntry.ActiveRequests++
		opEntry.LastSelectedService = service
		serviceOp := m.ensureServiceOperation(service, operation)
		serviceOp.ActiveRequests++
	}
}

func (m *Manager) EndSuccess(service, operation string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	entry := m.ensure(service)
	if entry.ActiveRequests > 0 {
		entry.ActiveRequests--
	}
	entry.SuccessCount++
	if operation != "" {
		opEntry := m.ensureOperation(operation)
		if opEntry.ActiveRequests > 0 {
			opEntry.ActiveRequests--
		}
		opEntry.SuccessCount++
		opEntry.LastSelectedService = service
		serviceOp := m.ensureServiceOperation(service, operation)
		if serviceOp.ActiveRequests > 0 {
			serviceOp.ActiveRequests--
		}
		serviceOp.SuccessCount++
	}
}

func (m *Manager) EndFailure(service, operation, category string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	entry := m.ensure(service)
	if entry.ActiveRequests > 0 {
		entry.ActiveRequests--
	}
	entry.FailureCount++
	entry.LastErrorCategory = category
	if operation != "" {
		opEntry := m.ensureOperation(operation)
		if opEntry.ActiveRequests > 0 {
			opEntry.ActiveRequests--
		}
		opEntry.FailureCount++
		opEntry.LastSelectedService = service
		opEntry.LastErrorCategory = category
		serviceOp := m.ensureServiceOperation(service, operation)
		if serviceOp.ActiveRequests > 0 {
			serviceOp.ActiveRequests--
		}
		serviceOp.FailureCount++
		serviceOp.LastErrorCategory = category
	}
}

func (m *Manager) RecordResolutionFailure(operation, category string) {
	if operation == "" {
		return
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	entry := m.ensureOperation(operation)
	entry.FailureCount++
	entry.LastErrorCategory = category
}

func (m *Manager) RecordCooldown(service, operation string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	entry := m.ensure(service)
	entry.CooldownCount++
	if operation != "" {
		serviceOp := m.ensureServiceOperation(service, operation)
		serviceOp.CooldownCount++
	}
}

func (m *Manager) Snapshot(service string) ServiceStats {
	m.mu.Lock()
	defer m.mu.Unlock()
	entry := m.ensure(service)
	return *entry
}

func (m *Manager) All() []ServiceStats {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]ServiceStats, 0, len(m.entries))
	for _, entry := range m.entries {
		out = append(out, *entry)
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].Service < out[j].Service
	})
	return out
}

func (m *Manager) OperationSnapshot(operation string) OperationStats {
	m.mu.Lock()
	defer m.mu.Unlock()
	entry := m.ensureOperation(operation)
	return *entry
}

func (m *Manager) Operations() []OperationStats {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]OperationStats, 0, len(m.operationEntries))
	for _, entry := range m.operationEntries {
		out = append(out, *entry)
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].Operation < out[j].Operation
	})
	return out
}

func (m *Manager) ServiceOperations() []ServiceOperationStats {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]ServiceOperationStats, 0)
	for _, byService := range m.serviceOperationRuns {
		for _, entry := range byService {
			out = append(out, *entry)
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Service == out[j].Service {
			return out[i].Operation < out[j].Operation
		}
		return out[i].Service < out[j].Service
	})
	return out
}

func (m *Manager) Reset() {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.entries = make(map[string]*ServiceStats)
	m.operationEntries = make(map[string]*OperationStats)
	m.serviceOperationRuns = make(map[string]map[string]*ServiceOperationStats)
}

func (m *Manager) ResetService(service string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.entries, service)
	delete(m.serviceOperationRuns, service)
}
