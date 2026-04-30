package registry

import (
	"sort"
	"strings"
	"sync"
	"time"
)

type Service struct {
	Name                string    `json:"name"`
	Language            string    `json:"language"`
	Endpoint            string    `json:"endpoint,omitempty"`
	Enabled             bool      `json:"enabled"`
	Healthy             bool      `json:"healthy"`
	HealthKnown         bool      `json:"health_known"`
	LastChecked         time.Time `json:"last_checked,omitempty"`
	LastError           string    `json:"last_error,omitempty"`
	SupportedOperations []string  `json:"supported_operations,omitempty"`
	operationSet        map[string]struct{}
}

func NewService(name, language, endpoint string, enabled bool, supportedOperations []string) Service {
	service := Service{
		Name:        name,
		Language:    language,
		Endpoint:    endpoint,
		Enabled:     enabled,
		Healthy:     enabled,
		HealthKnown: false,
	}
	service.SetSupportedOperations(supportedOperations)
	return service
}

func (s *Service) SetSupportedOperations(operations []string) {
	seen := make(map[string]struct{}, len(operations))
	normalizedOps := make([]string, 0, len(operations))
	for _, op := range operations {
		normalized := strings.TrimSpace(op)
		if normalized == "" {
			continue
		}
		if _, ok := seen[normalized]; ok {
			continue
		}
		seen[normalized] = struct{}{}
		normalizedOps = append(normalizedOps, normalized)
	}
	sort.Strings(normalizedOps)
	s.SupportedOperations = normalizedOps
	s.operationSet = seen
}

func (s Service) Supports(operation string) bool {
	if strings.TrimSpace(operation) == "" {
		return false
	}
	if len(s.operationSet) == 0 {
		return true
	}
	_, ok := s.operationSet[operation]
	return ok
}

type Registry struct {
	mu       sync.RWMutex
	services map[string]Service
}

func New() *Registry {
	return &Registry{services: make(map[string]Service)}
}

func (r *Registry) Register(service Service) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.services[service.Name] = service
}

func (r *Registry) UpdateHealth(name string, healthy bool, lastError string, checkedAt time.Time) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	service, ok := r.services[name]
	if !ok {
		return false
	}
	service.HealthKnown = true
	service.Healthy = healthy
	service.LastChecked = checkedAt
	service.LastError = strings.TrimSpace(lastError)
	r.services[name] = service
	return true
}

func (r *Registry) UpdateCapabilities(name string, supportedOperations []string) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	service, ok := r.services[name]
	if !ok {
		return false
	}
	service.SetSupportedOperations(supportedOperations)
	r.services[name] = service
	return true
}

func (r *Registry) SetEnabled(name string, enabled bool) (Service, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	service, ok := r.services[name]
	if !ok {
		return Service{}, false
	}
	service.Enabled = enabled
	r.services[name] = service
	return service, true
}

func (r *Registry) ResetHealth(name string) (Service, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	service, ok := r.services[name]
	if !ok {
		return Service{}, false
	}
	service.HealthKnown = false
	service.Healthy = service.Enabled
	service.LastChecked = time.Time{}
	service.LastError = ""
	r.services[name] = service
	return service, true
}

func (r *Registry) Get(name string) (Service, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	service, ok := r.services[name]
	return service, ok
}

func (r *Registry) List() []Service {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make([]Service, 0, len(r.services))
	for _, service := range r.services {
		out = append(out, service)
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].Name < out[j].Name
	})
	return out
}
