package services

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"easy_protocol/config"
	"easy_protocol/registry"
	"easy_protocol/transports"
)

type providerChildProcess struct {
	serviceName string
	providerID  string
	language    string
	endpoint    string
	port        int
	cmd         *exec.Cmd
	busy        bool
	healthy     bool
	stopping    bool
	lastIdleAt  time.Time
}

type providerProcessFamily struct {
	providerID       string
	language         string
	servicePrefix    string
	supportedOps     []string
	warmReplicas     int
	maxReplicas      int
	idleScaleDown    time.Duration
	acquireTimeout   time.Duration
	nextReplicaIndex int
	children         map[string]*providerChildProcess
}

type providerProcessPool struct {
	registry   *registry.Registry
	families   map[string]*providerProcessFamily
	mu         sync.Mutex
	stopCh     chan struct{}
	stopped    bool
	launcher   providerProcessLauncher
	httpClient *http.Client
}

type providerProcessLauncher func(ctx context.Context, family *providerProcessFamily, serviceName string, port int) (*exec.Cmd, string, error)

type providerProcessLease struct {
	pool        *providerProcessPool
	acquiredSvc string
}

func (l *providerProcessLease) Release() {
	if l == nil || l.pool == nil || strings.TrimSpace(l.acquiredSvc) == "" {
		return
	}
	l.pool.release(l.acquiredSvc)
}

var defaultProviderProcessLauncher providerProcessLauncher = launchPythonProviderProcess
var providerChildHealthWaiter = waitForProviderChildHealth
var providerChildCapabilitiesFetcher = fetchProviderChildCapabilities

func NewProviderProcessPool(cfg config.Config, reg *registry.Registry) *providerProcessPool {
	if reg == nil {
		return nil
	}

	families := buildProviderProcessFamilies(cfg, reg.List())
	if len(families) == 0 {
		return nil
	}

	pool := &providerProcessPool{
		registry: reg,
		families: families,
		stopCh:   make(chan struct{}),
		launcher: defaultProviderProcessLauncher,
		httpClient: &http.Client{
			Timeout: 5 * time.Second,
		},
	}
	return pool
}

func buildProviderProcessFamilies(cfg config.Config, services []registry.Service) map[string]*providerProcessFamily {
	out := make(map[string]*providerProcessFamily)
	for providerID, item := range cfg.ProviderPool.Providers {
		normalizedProviderID := strings.TrimSpace(providerID)
		if normalizedProviderID == "" {
			continue
		}
		servicePrefix, language, supportedOps := inferManagedServiceFamily(normalizedProviderID, services)
		if servicePrefix == "" {
			continue
		}
		out[servicePrefix] = &providerProcessFamily{
			providerID:       normalizedProviderID,
			language:         language,
			servicePrefix:    servicePrefix,
			supportedOps:     append([]string(nil), supportedOps...),
			warmReplicas:     item.WarmReplicas,
			maxReplicas:      item.MaxReplicas,
			idleScaleDown:    item.IdleScaleDownSeconds,
			acquireTimeout:   item.AcquireTimeout,
			nextReplicaIndex: 1,
			children:         make(map[string]*providerChildProcess),
		}
	}
	return out
}

func inferManagedServiceFamily(providerID string, services []registry.Service) (string, string, []string) {
	var defaultPrefix string
	switch strings.ToLower(providerID) {
	case "python":
		defaultPrefix = "PythonProtocol"
	case "go":
		defaultPrefix = "GolangProtocol"
	case "javascript":
		defaultPrefix = "JSProtocol"
	case "rust":
		defaultPrefix = "RustProtocol"
	default:
		return "", "", nil
	}

	for _, service := range services {
		if strings.HasPrefix(service.Name, defaultPrefix+"-") {
			return defaultPrefix, service.Language, service.SupportedOperations
		}
	}

	switch strings.ToLower(providerID) {
	case "python":
		return defaultPrefix, "python", []string{"codex.semantic.step"}
	case "go":
		return defaultPrefix, "go", nil
	case "javascript":
		return defaultPrefix, "javascript", nil
	case "rust":
		return defaultPrefix, "rust", nil
	default:
		return "", "", nil
	}
}

func (p *providerProcessPool) Start(ctx context.Context) error {
	if p == nil {
		return nil
	}

	for _, family := range p.families {
		for p.activeCountLocked(family) < family.warmReplicas {
			if _, err := p.spawnChild(ctx, family); err != nil {
				return err
			}
		}
	}

	go p.reconcileLoop()
	return nil
}

func (p *providerProcessPool) Close() {
	if p == nil {
		return
	}

	p.mu.Lock()
	if p.stopped {
		p.mu.Unlock()
		return
	}
	p.stopped = true
	close(p.stopCh)
	children := make([]*providerChildProcess, 0)
	for _, family := range p.families {
		for _, child := range family.children {
			child.stopping = true
			children = append(children, child)
		}
	}
	p.mu.Unlock()

	for _, child := range children {
		stopProviderChildProcess(child)
	}
}

func (p *providerProcessPool) Acquire(ctx context.Context, requestedService string) (string, transports.LeaseHandle, error) {
	if p == nil {
		return requestedService, nil, nil
	}

	family := p.familyForServiceName(requestedService)
	if family == nil {
		return requestedService, nil, nil
	}

	deadline := time.Now().Add(family.acquireTimeout)
	for {
		p.mu.Lock()
		if p.stopped {
			p.mu.Unlock()
			return "", nil, errors.New("provider process pool stopped")
		}
		p.pruneExitedChildrenLocked(family)
		if child := p.firstIdleChildLocked(family); child != nil {
			child.busy = true
			child.lastIdleAt = time.Time{}
			serviceName := child.serviceName
			p.mu.Unlock()
			return serviceName, &providerProcessLease{pool: p, acquiredSvc: serviceName}, nil
		}

		canGrow := p.activeCountLocked(family) < family.maxReplicas
		p.mu.Unlock()
		if canGrow {
			child, err := p.spawnChild(ctx, family)
			if err != nil {
				return "", nil, err
			}
			p.mu.Lock()
			if existing, ok := family.children[child.serviceName]; ok {
				existing.busy = true
				existing.lastIdleAt = time.Time{}
			}
			p.mu.Unlock()
			return child.serviceName, &providerProcessLease{pool: p, acquiredSvc: child.serviceName}, nil
		}

		if ctx.Err() != nil {
			return "", nil, ctx.Err()
		}
		if time.Now().After(deadline) {
			return "", nil, fmt.Errorf("provider family %s acquire timeout", family.servicePrefix)
		}
		time.Sleep(200 * time.Millisecond)
	}
}

func (p *providerProcessPool) release(serviceName string) {
	p.mu.Lock()
	defer p.mu.Unlock()
	family := p.familyForServiceName(serviceName)
	if family == nil {
		return
	}
	child, ok := family.children[serviceName]
	if !ok {
		return
	}
	child.busy = false
	child.lastIdleAt = time.Now()
}

func (p *providerProcessPool) reconcileLoop() {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			p.reconcileFamilies()
		case <-p.stopCh:
			return
		}
	}
}

func (p *providerProcessPool) reconcileFamilies() {
	for _, family := range p.families {
		p.reconcileFamily(family)
	}
}

func (p *providerProcessPool) reconcileFamily(family *providerProcessFamily) {
	p.mu.Lock()
	if p.stopped {
		p.mu.Unlock()
		return
	}
	p.pruneExitedChildrenLocked(family)
	activeCount := p.activeCountLocked(family)
	p.mu.Unlock()

	for activeCount < family.warmReplicas {
		if _, err := p.spawnChild(context.Background(), family); err != nil {
			return
		}
		activeCount++
	}

	p.mu.Lock()
	defer p.mu.Unlock()
	if family.idleScaleDown <= 0 {
		return
	}
	activeCount = p.activeCountLocked(family)
	if activeCount <= family.warmReplicas {
		return
	}
	now := time.Now()
	for _, child := range family.children {
		if activeCount <= family.warmReplicas {
			break
		}
		if child == nil || child.busy || child.stopping || child.lastIdleAt.IsZero() {
			continue
		}
		if now.Sub(child.lastIdleAt) < family.idleScaleDown {
			continue
		}
		child.stopping = true
		go stopProviderChildProcess(child)
		delete(family.children, child.serviceName)
		p.registry.Remove(child.serviceName)
		activeCount--
	}
}

func (p *providerProcessPool) spawnChild(ctx context.Context, family *providerProcessFamily) (*providerChildProcess, error) {
	if p == nil || family == nil {
		return nil, errors.New("provider process pool unavailable")
	}
	port, err := findFreeLocalPort()
	if err != nil {
		return nil, err
	}

	p.mu.Lock()
	serviceName := nextFamilyServiceNameLocked(family)
	p.mu.Unlock()

	cmd, endpoint, err := p.launcher(ctx, family, serviceName, port)
	if err != nil {
		return nil, err
	}
	child := &providerChildProcess{
		serviceName: serviceName,
		providerID:  family.providerID,
		language:    family.language,
		endpoint:    endpoint,
		port:        port,
		cmd:         cmd,
		healthy:     true,
		lastIdleAt:  time.Now(),
	}

	if err := providerChildHealthWaiter(ctx, p.httpClient, endpoint); err != nil {
		stopProviderChildProcess(child)
		return nil, err
	}

	ops, err := providerChildCapabilitiesFetcher(ctx, p.httpClient, endpoint)
	if err == nil && len(ops) > 0 {
		child.healthy = true
		family.supportedOps = ops
	}

	p.mu.Lock()
	defer p.mu.Unlock()
	if p.stopped {
		go stopProviderChildProcess(child)
		return nil, errors.New("provider process pool stopped")
	}
	family.children[serviceName] = child
	p.registry.Register(registry.NewService(serviceName, family.language, endpoint, true, family.supportedOps))
	return child, nil
}

func (p *providerProcessPool) familyForServiceName(serviceName string) *providerProcessFamily {
	for prefix, family := range p.families {
		if serviceName == prefix || strings.HasPrefix(serviceName, prefix+"-") {
			return family
		}
	}
	return nil
}

func (p *providerProcessPool) pruneExitedChildrenLocked(family *providerProcessFamily) {
	for serviceName, child := range family.children {
		if child == nil || child.cmd == nil || child.cmd.Process == nil {
			continue
		}
		if child.cmd.ProcessState != nil && child.cmd.ProcessState.Exited() {
			delete(family.children, serviceName)
			p.registry.Remove(serviceName)
		}
	}
}

func (p *providerProcessPool) activeCountLocked(family *providerProcessFamily) int {
	count := 0
	for _, child := range family.children {
		if child != nil && !child.stopping {
			count++
		}
	}
	return count
}

func (p *providerProcessPool) firstIdleChildLocked(family *providerProcessFamily) *providerChildProcess {
	var chosen *providerChildProcess
	for _, child := range family.children {
		if child == nil || child.busy || child.stopping || !child.healthy {
			continue
		}
		if chosen == nil || child.lastIdleAt.Before(chosen.lastIdleAt) {
			chosen = child
		}
	}
	return chosen
}

func nextFamilyServiceNameLocked(family *providerProcessFamily) string {
	for {
		name := fmt.Sprintf("%s-%03d", family.servicePrefix, family.nextReplicaIndex)
		family.nextReplicaIndex++
		if _, exists := family.children[name]; !exists {
			return name
		}
	}
}

func waitForProviderChildHealth(ctx context.Context, client *http.Client, endpoint string) error {
	healthURL := strings.TrimRight(endpoint, "/") + "/health"
	deadline := time.Now().Add(20 * time.Second)
	for {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, healthURL, nil)
		if err == nil {
			resp, callErr := client.Do(req)
			if callErr == nil {
				defer resp.Body.Close()
				if resp.StatusCode < http.StatusBadRequest {
					var payload struct {
						Status string `json:"status"`
					}
					if decodeErr := json.NewDecoder(resp.Body).Decode(&payload); decodeErr == nil && strings.EqualFold(payload.Status, "ok") {
						return nil
					}
				}
			}
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("provider child health timeout for %s", endpoint)
		}
		time.Sleep(500 * time.Millisecond)
	}
}

func fetchProviderChildCapabilities(ctx context.Context, client *http.Client, endpoint string) ([]string, error) {
	capURL := strings.TrimRight(endpoint, "/") + "/capabilities"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, capURL, nil)
	if err != nil {
		return nil, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= http.StatusBadRequest {
		return nil, fmt.Errorf("capabilities returned status %d", resp.StatusCode)
	}
	var payload struct {
		Operations []string `json:"operations"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		return nil, err
	}
	return payload.Operations, nil
}

func findFreeLocalPort() (int, error) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, err
	}
	defer listener.Close()
	addr, ok := listener.Addr().(*net.TCPAddr)
	if !ok {
		return 0, errors.New("unexpected listener addr type")
	}
	return addr.Port, nil
}

func stopProviderChildProcess(child *providerChildProcess) {
	if child == nil || child.cmd == nil || child.cmd.Process == nil {
		return
	}
	_ = child.cmd.Process.Kill()
	_, _ = child.cmd.Process.Wait()
}

func launchPythonProviderProcess(ctx context.Context, family *providerProcessFamily, serviceName string, port int) (*exec.Cmd, string, error) {
	pythonPath, err := resolveProviderPythonExecutable()
	if err != nil {
		return nil, "", err
	}
	serverPath, searchPaths, err := resolvePythonProviderServerPath()
	if err != nil {
		return nil, "", err
	}

	cmd := exec.CommandContext(ctx, pythonPath, serverPath)
	env := os.Environ()
	env = upsertProviderEnv(env, "PYTHON_PROTOCOL_HOST", "127.0.0.1")
	env = upsertProviderEnv(env, "PYTHON_PROTOCOL_PORT", fmt.Sprintf("%d", port))
	env = upsertProviderEnv(env, "PYTHON_PROTOCOL_MIN_WARM_WORKERS", coalesceProviderEnv("PYTHON_PROTOCOL_MIN_WARM_WORKERS", "1"))
	env = upsertProviderEnv(env, "PYTHON_PROTOCOL_MAX_WORKERS", coalesceProviderEnv("PYTHON_PROTOCOL_MAX_WORKERS", "1"))
	env = upsertProviderEnv(env, "PYTHON_PROTOCOL_IDLE_TIMEOUT_SECONDS", coalesceProviderEnv("PYTHON_PROTOCOL_IDLE_TIMEOUT_SECONDS", "3600"))
	env = upsertProviderEnv(env, "PYTHON_PROTOCOL_TASK_TIMEOUT_SECONDS", coalesceProviderEnv("PYTHON_PROTOCOL_TASK_TIMEOUT_SECONDS", "1800"))
	env = upsertProviderEnv(env, "PYTHON_PROTOCOL_ACQUIRE_TIMEOUT_SECONDS", coalesceProviderEnv("PYTHON_PROTOCOL_ACQUIRE_TIMEOUT_SECONDS", "60"))
	env = upsertProviderEnv(env, "PYTHON_PROTOCOL_MAX_TASKS_PER_WORKER", coalesceProviderEnv("PYTHON_PROTOCOL_MAX_TASKS_PER_WORKER", "1000"))
	env = upsertProviderEnv(env, "PYTHON_PROTOCOL_REAPER_INTERVAL_SECONDS", coalesceProviderEnv("PYTHON_PROTOCOL_REAPER_INTERVAL_SECONDS", "30"))
	env = upsertProviderEnv(env, "PYTHONPATH", strings.Join(searchPaths, string(os.PathListSeparator)))
	env = upsertProviderEnv(env, "EASY_PROTOCOL_CHILD_SERVICE_NAME", serviceName)
	cmd.Env = env
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		return nil, "", err
	}
	return cmd, fmt.Sprintf("http://127.0.0.1:%d", port), nil
}

func resolveProviderPythonExecutable() (string, error) {
	if custom := strings.TrimSpace(os.Getenv("EASY_PROTOCOL_PROVIDER_PYTHON_BIN")); custom != "" {
		return custom, nil
	}
	if path, err := exec.LookPath("python"); err == nil {
		return path, nil
	}
	if path, err := exec.LookPath("python3"); err == nil {
		return path, nil
	}
	return "", errors.New("python executable not found for managed provider child")
}

func resolvePythonProviderServerPath() (string, []string, error) {
	if custom := strings.TrimSpace(os.Getenv("EASY_PROTOCOL_PROVIDER_PYTHON_SERVER_PATH")); custom != "" {
		searchPaths := []string{filepath.Dir(custom)}
		return custom, searchPaths, nil
	}

	cwd, _ := os.Getwd()
	candidates := []string{
		filepath.Join(cwd, "..", "..", "providers", "python", "src", "server.py"),
		filepath.Join(cwd, "..", "..", "providers", "python", "src"),
		filepath.Join(cwd, "..", "..", "providers", "python", "python_shared", "src"),
		"/opt/easy-protocol/providers/python/src/server.py",
	}

	for _, candidate := range candidates {
		if strings.HasSuffix(candidate, ".py") {
			if _, err := os.Stat(candidate); err == nil {
				serverDir := filepath.Dir(candidate)
				searchPaths := []string{
					serverDir,
					filepath.Join(filepath.Dir(serverDir), "python_shared", "src"),
					"/opt/easy-protocol/providers/python/src",
					"/opt/easy-protocol/providers/python/python_shared/src",
				}
				return candidate, compactSearchPaths(searchPaths), nil
			}
		}
	}

	if _, err := os.Stat("/opt/easy-protocol/providers/python/src/server.py"); err == nil {
		searchPaths := []string{
			"/opt/easy-protocol/providers/python/src",
			"/opt/easy-protocol/providers/python/python_shared/src",
		}
		return "/opt/easy-protocol/providers/python/src/server.py", compactSearchPaths(searchPaths), nil
	}

	return "", nil, errors.New("managed python provider server path not found")
}

func compactSearchPaths(paths []string) []string {
	seen := make(map[string]struct{}, len(paths))
	out := make([]string, 0, len(paths))
	for _, item := range paths {
		trimmed := strings.TrimSpace(item)
		if trimmed == "" {
			continue
		}
		if _, ok := seen[trimmed]; ok {
			continue
		}
		seen[trimmed] = struct{}{}
		out = append(out, trimmed)
	}
	return out
}

func upsertProviderEnv(env []string, key, value string) []string {
	prefix := key + "="
	for index, entry := range env {
		if strings.HasPrefix(strings.ToUpper(entry), strings.ToUpper(prefix)) {
			env[index] = prefix + value
			return env
		}
	}
	return append(env, prefix+value)
}

func coalesceProviderEnv(key, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}
