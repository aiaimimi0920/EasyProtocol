package services

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"

	"easy_protocol/config"
	"easy_protocol/registry"
	"easy_protocol/transports"
)

type providerContainerChild struct {
	serviceName   string
	providerID    string
	language      string
	endpoint      string
	containerName string
	containerID   string
	port          int
	busy          bool
	healthy       bool
	stopping      bool
	lastIdleAt    time.Time
	managed       bool
}

type providerContainerFamily struct {
	providerID          string
	language            string
	servicePrefix       string
	containerNamePrefix string
	endpointHostPrefix  string
	image               string
	networkName         string
	composeProject      string
	port                int
	supportedOps        []string
	environment         map[string]string
	hostMounts          []config.ManagedProviderHostMountConfig
	warmReplicas        int
	maxReplicas         int
	idleScaleDown       time.Duration
	acquireTimeout      time.Duration
	publishedPortBase   int
	nextReplicaIndex    int
	children            map[string]*providerContainerChild
}

type providerContainerPool struct {
	registry   *registry.Registry
	families   map[string]*providerContainerFamily
	mu         sync.Mutex
	stopCh     chan struct{}
	stopped    bool
	docker     providerContainerDocker
	httpClient *http.Client
}

type providerContainerLease struct {
	pool        *providerContainerPool
	acquiredSvc string
}

type providerContainerDocker interface {
	CreateContainer(ctx context.Context, name string, request dockerContainerCreateRequest) (string, error)
	StartContainer(ctx context.Context, id string) error
	StopContainer(ctx context.Context, name string, timeoutSeconds int) error
	RemoveContainer(ctx context.Context, name string, force bool) error
	InspectContainer(ctx context.Context, name string) (dockerContainerInspect, error)
}

func (l *providerContainerLease) Release() {
	if l == nil || l.pool == nil || strings.TrimSpace(l.acquiredSvc) == "" {
		return
	}
	l.pool.release(l.acquiredSvc)
}

func NewProviderContainerPool(cfg config.Config, reg *registry.Registry) *providerContainerPool {
	if reg == nil || !cfg.ManagedProviderRuntime.Enabled {
		return nil
	}

	families := buildProviderContainerFamilies(cfg)
	if len(families) == 0 {
		return nil
	}

	dockerClient, err := newDockerContainerClient(strings.TrimSpace(cfg.ManagedProviderRuntime.DockerHost))
	if err != nil {
		return nil
	}

	return &providerContainerPool{
		registry: reg,
		families: families,
		stopCh:   make(chan struct{}),
		docker:   dockerClient,
		httpClient: &http.Client{
			Timeout: 5 * time.Second,
		},
	}
}

func buildProviderContainerFamilies(cfg config.Config) map[string]*providerContainerFamily {
	out := make(map[string]*providerContainerFamily)
	for providerID, runtimeProvider := range cfg.ManagedProviderRuntime.Providers {
		if !runtimeProvider.Enabled {
			continue
		}
		normalizedProviderID := strings.TrimSpace(providerID)
		if normalizedProviderID == "" {
			continue
		}
		poolCfg, ok := cfg.ProviderPool.Providers[normalizedProviderID]
		if !ok {
			continue
		}
		servicePrefix := strings.TrimSpace(runtimeProvider.ServiceNamePrefix)
		if servicePrefix == "" {
			servicePrefix = defaultManagedServicePrefix(normalizedProviderID)
		}
		containerNamePrefix := strings.TrimSpace(runtimeProvider.ContainerNamePrefix)
		if containerNamePrefix == "" {
			containerNamePrefix = defaultManagedContainerPrefix(normalizedProviderID)
		}
		endpointHostPrefix := strings.TrimSpace(runtimeProvider.EndpointHostPrefix)
		if endpointHostPrefix == "" {
			endpointHostPrefix = containerNamePrefix
		}
		image := strings.TrimSpace(runtimeProvider.Image)
		if image == "" {
			continue
		}
		language := inferManagedLanguage(normalizedProviderID)
		supportedOps := append([]string(nil), runtimeProvider.SupportedOperations...)
		if len(supportedOps) == 0 {
			supportedOps = inferManagedSupportedOperations(cfg.Services, servicePrefix)
		}
		out[servicePrefix] = &providerContainerFamily{
			providerID:          normalizedProviderID,
			language:            language,
			servicePrefix:       servicePrefix,
			containerNamePrefix: containerNamePrefix,
			endpointHostPrefix:  endpointHostPrefix,
			image:               image,
			networkName:         strings.TrimSpace(cfg.ManagedProviderRuntime.NetworkName),
			composeProject:      strings.TrimSpace(cfg.ManagedProviderRuntime.ComposeProject),
			port:                runtimeProvider.Port,
			supportedOps:        supportedOps,
			environment:         cloneManagedEnvironment(runtimeProvider.Environment),
			hostMounts:          append([]config.ManagedProviderHostMountConfig(nil), runtimeProvider.HostMounts...),
			warmReplicas:        poolCfg.WarmReplicas,
			maxReplicas:         poolCfg.MaxReplicas,
			idleScaleDown:       poolCfg.IdleScaleDownSeconds,
			acquireTimeout:      poolCfg.AcquireTimeout,
			publishedPortBase:   runtimeProvider.PublishedPortBase,
			nextReplicaIndex:    1,
			children:            make(map[string]*providerContainerChild),
		}
	}
	return out
}

func cloneManagedEnvironment(in map[string]string) map[string]string {
	if len(in) == 0 {
		return map[string]string{}
	}
	out := make(map[string]string, len(in))
	for key, value := range in {
		out[strings.TrimSpace(key)] = value
	}
	return out
}

func defaultManagedServicePrefix(providerID string) string {
	switch strings.ToLower(strings.TrimSpace(providerID)) {
	case "python":
		return "PythonProtocol"
	case "go":
		return "GolangProtocol"
	case "javascript":
		return "JSProtocol"
	case "rust":
		return "RustProtocol"
	default:
		return strings.Title(providerID) + "Protocol"
	}
}

func defaultManagedContainerPrefix(providerID string) string {
	switch strings.ToLower(strings.TrimSpace(providerID)) {
	case "python":
		return "easy-protocol-python"
	case "go":
		return "easy-protocol-go"
	case "javascript":
		return "easy-protocol-javascript"
	case "rust":
		return "easy-protocol-rust"
	default:
		return "easy-protocol-" + strings.ToLower(strings.TrimSpace(providerID))
	}
}

func inferManagedLanguage(providerID string) string {
	switch strings.ToLower(strings.TrimSpace(providerID)) {
	case "python":
		return "python"
	case "go":
		return "go"
	case "javascript":
		return "javascript"
	case "rust":
		return "rust"
	default:
		return strings.ToLower(strings.TrimSpace(providerID))
	}
}

func inferManagedSupportedOperations(services []config.ServiceConfig, servicePrefix string) []string {
	for _, service := range services {
		if service.Name == servicePrefix+"-001" || strings.HasPrefix(service.Name, servicePrefix+"-") {
			return append([]string(nil), service.SupportedOperations...)
		}
	}
	return nil
}

func (p *providerContainerPool) Start(ctx context.Context) error {
	if p == nil {
		return nil
	}
	for _, family := range p.families {
		if err := p.adoptExistingChildren(ctx, family); err != nil {
			return err
		}
		for p.activeCountLocked(family) < family.warmReplicas {
			if _, err := p.spawnChild(ctx, family); err != nil {
				return err
			}
		}
	}
	go p.reconcileLoop()
	return nil
}

func (p *providerContainerPool) Close() {
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
	children := make([]*providerContainerChild, 0)
	for _, family := range p.families {
		for _, child := range family.children {
			if child == nil {
				continue
			}
			child.stopping = true
			children = append(children, child)
		}
	}
	p.mu.Unlock()

	for _, child := range children {
		if child.managed {
			_ = p.stopAndRemoveChild(context.Background(), child)
		}
	}
}

func (p *providerContainerPool) Acquire(ctx context.Context, requestedService string) (string, transports.LeaseHandle, error) {
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
			return "", nil, errors.New("provider container pool stopped")
		}
		p.pruneExitedChildrenLocked(ctx, family)
		if child := p.firstIdleChildLocked(family); child != nil {
			child.busy = true
			child.lastIdleAt = time.Time{}
			serviceName := child.serviceName
			p.mu.Unlock()
			return serviceName, &providerContainerLease{pool: p, acquiredSvc: serviceName}, nil
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
			return child.serviceName, &providerContainerLease{pool: p, acquiredSvc: child.serviceName}, nil
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

func (p *providerContainerPool) release(serviceName string) {
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

func (p *providerContainerPool) reconcileLoop() {
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

func (p *providerContainerPool) reconcileFamilies() {
	for _, family := range p.families {
		p.reconcileFamily(family)
	}
}

func (p *providerContainerPool) reconcileFamily(family *providerContainerFamily) {
	p.mu.Lock()
	if p.stopped {
		p.mu.Unlock()
		return
	}
	p.pruneExitedChildrenLocked(context.Background(), family)
	activeCount := p.activeCountLocked(family)
	p.mu.Unlock()

	for activeCount < family.warmReplicas {
		if _, err := p.spawnChild(context.Background(), family); err != nil {
			return
		}
		activeCount++
	}

	p.mu.Lock()
	if family.idleScaleDown <= 0 {
		p.mu.Unlock()
		return
	}
	activeCount = p.activeCountLocked(family)
	if activeCount <= family.warmReplicas {
		p.mu.Unlock()
		return
	}
	now := time.Now()
	candidates := make([]*providerContainerChild, 0)
	for _, child := range family.children {
		if child == nil || child.busy || child.stopping || child.lastIdleAt.IsZero() {
			continue
		}
		if now.Sub(child.lastIdleAt) < family.idleScaleDown {
			continue
		}
		candidates = append(candidates, child)
	}
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].managed != candidates[j].managed {
			return candidates[i].managed
		}
		return candidates[i].lastIdleAt.Before(candidates[j].lastIdleAt)
	})
	toStop := make([]*providerContainerChild, 0)
	for _, child := range candidates {
		if activeCount <= family.warmReplicas {
			break
		}
		if !child.managed {
			continue
		}
		child.stopping = true
		delete(family.children, child.serviceName)
		p.registry.Remove(child.serviceName)
		toStop = append(toStop, child)
		activeCount--
	}
	p.mu.Unlock()

	for _, child := range toStop {
		_ = p.stopAndRemoveChild(context.Background(), child)
	}
}

func (p *providerContainerPool) adoptExistingChildren(ctx context.Context, family *providerContainerFamily) error {
	for index := 1; index <= family.maxReplicas; index++ {
		serviceName := formatFamilyServiceName(family.servicePrefix, index)
		containerName := formatFamilyContainerName(family.containerNamePrefix, index)
		inspect, err := p.docker.InspectContainer(ctx, containerName)
		if err != nil {
			if errors.Is(err, errDockerContainerNotFound) {
				continue
			}
			return err
		}
		if !inspect.State.Running {
			continue
		}
		managedChild := strings.EqualFold(inspect.Config.Labels["easyprotocol.managed_child"], "true")
		if managedChild && (!dockerContainerImageMatches(inspect.Config.Image, family.image) ||
			!dockerContainerEnvMatches(inspect.Config.Env, buildManagedProviderEnv(family, serviceName))) {
			_ = p.stopAndRemoveChild(ctx, &providerContainerChild{
				containerName: containerName,
				managed:       true,
			})
			continue
		}
		child := &providerContainerChild{
			serviceName:   serviceName,
			providerID:    family.providerID,
			language:      family.language,
			endpoint:      formatFamilyEndpoint(family.endpointHostPrefix, family.port, index),
			containerName: containerName,
			containerID:   inspect.ID,
			port:          family.port,
			healthy:       true,
			lastIdleAt:    time.Now(),
			managed:       managedChild,
		}
		if index >= family.nextReplicaIndex {
			family.nextReplicaIndex = index + 1
		}
		if err := providerChildHealthWaiter(ctx, p.httpClient, child.endpoint); err != nil {
			continue
		}
		ops, err := providerChildCapabilitiesFetcher(ctx, p.httpClient, child.endpoint)
		if err == nil && len(ops) > 0 {
			child.healthy = true
			family.supportedOps = ops
		}
		family.children[serviceName] = child
		p.registry.Register(registry.NewService(serviceName, family.language, child.endpoint, true, family.supportedOps))
	}
	return nil
}

func (p *providerContainerPool) spawnChild(ctx context.Context, family *providerContainerFamily) (*providerContainerChild, error) {
	if p == nil || family == nil {
		return nil, errors.New("provider container pool unavailable")
	}

	p.mu.Lock()
	serviceName := nextContainerFamilyServiceNameLocked(family)
	replicaIndex := parseFamilyReplicaIndex(serviceName)
	containerName := formatFamilyContainerName(family.containerNamePrefix, replicaIndex)
	endpoint := formatFamilyEndpoint(family.endpointHostPrefix, family.port, replicaIndex)
	p.mu.Unlock()

	createRequest := dockerContainerCreateRequest{
		Image: family.image,
		Env:   buildManagedProviderEnv(family, serviceName),
		Labels: map[string]string{
			"easyprotocol.managed_child": "true",
			"easyprotocol.provider_id":   family.providerID,
			"easyprotocol.service_name":  serviceName,
			"com.docker.compose.project": family.composeProject,
			"com.docker.compose.service": containerName,
			"com.docker.compose.oneoff":  "False",
		},
		ExposedPorts: map[string]map[string]string{
			fmt.Sprintf("%d/tcp", family.port): {},
		},
		HostConfig: dockerContainerHostConfig{
			Binds:       buildManagedProviderBinds(family.hostMounts),
			NetworkMode: family.networkName,
			RestartPolicy: dockerRestartPolicy{
				Name: "unless-stopped",
			},
		},
		NetworkingConfig: dockerNetworkingConfig{
			EndpointsConfig: map[string]dockerEndpointSettings{
				family.networkName: {
					Aliases: []string{containerName},
				},
			},
		},
	}
	if family.publishedPortBase > 0 && replicaIndex == 1 {
		portKey := fmt.Sprintf("%d/tcp", family.port)
		createRequest.HostConfig.PortBindings = map[string][]dockerPortBinding{
			portKey: {{
				HostPort: fmt.Sprintf("%d", family.publishedPortBase),
			}},
		}
	}

	containerID, err := p.docker.CreateContainer(ctx, containerName, createRequest)
	if err != nil {
		if dockerContainerNameConflict(err) {
			if child, adoptErr := p.adoptConflictingChild(ctx, family, serviceName, containerName, endpoint); adoptErr == nil {
				return child, nil
			}
		}
		return nil, err
	}
	if err := p.docker.StartContainer(ctx, containerID); err != nil {
		_ = p.docker.RemoveContainer(context.Background(), containerName, true)
		return nil, err
	}

	child := &providerContainerChild{
		serviceName:   serviceName,
		providerID:    family.providerID,
		language:      family.language,
		endpoint:      endpoint,
		containerName: containerName,
		containerID:   containerID,
		port:          family.port,
		healthy:       true,
		lastIdleAt:    time.Now(),
		managed:       true,
	}

	if err := providerChildHealthWaiter(ctx, p.httpClient, endpoint); err != nil {
		_ = p.stopAndRemoveChild(context.Background(), child)
		return nil, err
	}

	ops, err := providerChildCapabilitiesFetcher(ctx, p.httpClient, endpoint)
	if err == nil && len(ops) > 0 {
		child.healthy = true
		family.supportedOps = ops
	}

	p.mu.Lock()
	if p.stopped {
		p.mu.Unlock()
		_ = p.stopAndRemoveChild(context.Background(), child)
		return nil, errors.New("provider container pool stopped")
	}
	family.children[serviceName] = child
	p.registry.Register(registry.NewService(serviceName, family.language, endpoint, true, family.supportedOps))
	p.mu.Unlock()
	return child, nil
}

func (p *providerContainerPool) adoptConflictingChild(
	ctx context.Context,
	family *providerContainerFamily,
	serviceName string,
	containerName string,
	endpoint string,
) (*providerContainerChild, error) {
	inspect, err := p.docker.InspectContainer(ctx, containerName)
	if err != nil {
		return nil, err
	}
	if !inspect.State.Running {
		return nil, fmt.Errorf("conflicting provider container %s is not running", containerName)
	}
	if !strings.EqualFold(inspect.Config.Labels["easyprotocol.managed_child"], "true") {
		return nil, fmt.Errorf("conflicting provider container %s is not an EasyProtocol managed child", containerName)
	}
	if !dockerContainerImageMatches(inspect.Config.Image, family.image) {
		return nil, fmt.Errorf("conflicting provider container %s image %q does not match %q", containerName, inspect.Config.Image, family.image)
	}
	if !dockerContainerEnvMatches(inspect.Config.Env, buildManagedProviderEnv(family, serviceName)) {
		return nil, fmt.Errorf("conflicting provider container %s environment does not match desired spec", containerName)
	}

	child := &providerContainerChild{
		serviceName:   serviceName,
		providerID:    family.providerID,
		language:      family.language,
		endpoint:      endpoint,
		containerName: containerName,
		containerID:   inspect.ID,
		port:          family.port,
		healthy:       true,
		lastIdleAt:    time.Now(),
		managed:       true,
	}

	if err := providerChildHealthWaiter(ctx, p.httpClient, endpoint); err != nil {
		return nil, err
	}

	ops, err := providerChildCapabilitiesFetcher(ctx, p.httpClient, endpoint)
	if err == nil && len(ops) > 0 {
		child.healthy = true
		family.supportedOps = ops
	}

	p.mu.Lock()
	if p.stopped {
		p.mu.Unlock()
		return nil, errors.New("provider container pool stopped")
	}
	family.children[serviceName] = child
	p.registry.Register(registry.NewService(serviceName, family.language, endpoint, true, family.supportedOps))
	p.mu.Unlock()
	return child, nil
}

func dockerContainerNameConflict(err error) bool {
	if err == nil {
		return false
	}
	message := strings.ToLower(err.Error())
	return strings.Contains(message, "status=409") ||
		(strings.Contains(message, "conflict") && strings.Contains(message, "already in use"))
}

func (p *providerContainerPool) stopAndRemoveChild(ctx context.Context, child *providerContainerChild) error {
	if p == nil || child == nil || !child.managed {
		return nil
	}
	_ = p.docker.StopContainer(ctx, child.containerName, 1)
	return p.docker.RemoveContainer(ctx, child.containerName, true)
}

func (p *providerContainerPool) familyForServiceName(serviceName string) *providerContainerFamily {
	for prefix, family := range p.families {
		if serviceName == prefix || strings.HasPrefix(serviceName, prefix+"-") {
			return family
		}
	}
	return nil
}

func (p *providerContainerPool) pruneExitedChildrenLocked(ctx context.Context, family *providerContainerFamily) {
	for serviceName, child := range family.children {
		if child == nil {
			continue
		}
		inspect, err := p.docker.InspectContainer(ctx, child.containerName)
		if err != nil {
			if errors.Is(err, errDockerContainerNotFound) {
				delete(family.children, serviceName)
				p.registry.Remove(serviceName)
			}
			continue
		}
		if !inspect.State.Running {
			delete(family.children, serviceName)
			p.registry.Remove(serviceName)
		}
	}
}

func (p *providerContainerPool) activeCountLocked(family *providerContainerFamily) int {
	count := 0
	for _, child := range family.children {
		if child != nil && !child.stopping {
			count++
		}
	}
	return count
}

func (p *providerContainerPool) firstIdleChildLocked(family *providerContainerFamily) *providerContainerChild {
	var chosen *providerContainerChild
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

func nextContainerFamilyServiceNameLocked(family *providerContainerFamily) string {
	for {
		name := formatFamilyServiceName(family.servicePrefix, family.nextReplicaIndex)
		family.nextReplicaIndex++
		if _, exists := family.children[name]; !exists {
			return name
		}
	}
}

func formatFamilyServiceName(prefix string, index int) string {
	return fmt.Sprintf("%s-%03d", prefix, maxManagedReplicaIndex(index))
}

func formatFamilyContainerName(prefix string, index int) string {
	return fmt.Sprintf("%s-%03d", prefix, maxManagedReplicaIndex(index))
}

func formatFamilyEndpoint(prefix string, port int, index int) string {
	return fmt.Sprintf("http://%s-%03d:%d", prefix, maxManagedReplicaIndex(index), port)
}

func parseFamilyReplicaIndex(serviceName string) int {
	lastDash := strings.LastIndex(serviceName, "-")
	if lastDash < 0 {
		return 1
	}
	var index int
	if _, err := fmt.Sscanf(serviceName[lastDash+1:], "%d", &index); err != nil || index <= 0 {
		return 1
	}
	return index
}

func maxManagedReplicaIndex(index int) int {
	if index <= 0 {
		return 1
	}
	return index
}

func buildManagedProviderEnv(family *providerContainerFamily, serviceName string) []string {
	keys := make([]string, 0, len(family.environment)+1)
	for key := range family.environment {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	env := make([]string, 0, len(keys)+1)
	for _, key := range keys {
		env = append(env, key+"="+family.environment[key])
	}
	env = append(env, "EASY_PROTOCOL_CHILD_SERVICE_NAME="+serviceName)
	return env
}

func dockerContainerEnvMatches(actual []string, expected []string) bool {
	actualByKey := make(map[string]string, len(actual))
	for _, item := range actual {
		key, value, ok := strings.Cut(item, "=")
		if !ok {
			continue
		}
		actualByKey[key] = value
	}
	for _, item := range expected {
		key, value, ok := strings.Cut(item, "=")
		if !ok {
			continue
		}
		actualValue, exists := actualByKey[key]
		if !exists || actualValue != value {
			return false
		}
	}
	return true
}

func buildManagedProviderBinds(mounts []config.ManagedProviderHostMountConfig) []string {
	out := make([]string, 0, len(mounts))
	for _, mount := range mounts {
		source := strings.TrimSpace(mount.Source)
		target := strings.TrimSpace(mount.Target)
		if source == "" || target == "" {
			continue
		}
		mode := "rw"
		if mount.ReadOnly {
			mode = "ro"
		}
		out = append(out, fmt.Sprintf("%s:%s:%s", source, target, mode))
	}
	return out
}

func dockerContainerImageMatches(actual string, expected string) bool {
	normalizedActual := strings.TrimSpace(actual)
	normalizedExpected := strings.TrimSpace(expected)
	if normalizedActual == "" || normalizedExpected == "" {
		return true
	}
	return normalizedActual == normalizedExpected || strings.HasPrefix(normalizedActual, normalizedExpected+"@")
}

var errDockerContainerNotFound = errors.New("docker container not found")

type dockerContainerClient struct {
	baseURL string
	client  *http.Client
}

func newDockerContainerClient(host string) (*dockerContainerClient, error) {
	normalized := strings.TrimSpace(host)
	if normalized == "" {
		normalized = "unix:///var/run/docker.sock"
	}
	if strings.HasPrefix(normalized, "unix://") {
		socketPath := strings.TrimPrefix(normalized, "unix://")
		transport := &http.Transport{
			DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
				var d net.Dialer
				return d.DialContext(ctx, "unix", socketPath)
			},
		}
		return &dockerContainerClient{
			baseURL: "http://docker",
			client:  &http.Client{Transport: transport, Timeout: 30 * time.Second},
		}, nil
	}
	if strings.HasPrefix(normalized, "http://") || strings.HasPrefix(normalized, "https://") {
		return &dockerContainerClient{
			baseURL: strings.TrimRight(normalized, "/"),
			client:  &http.Client{Timeout: 30 * time.Second},
		}, nil
	}
	return nil, fmt.Errorf("unsupported docker host: %s", host)
}

type dockerContainerCreateRequest struct {
	Image            string                       `json:"Image"`
	Env              []string                     `json:"Env,omitempty"`
	Labels           map[string]string            `json:"Labels,omitempty"`
	ExposedPorts     map[string]map[string]string `json:"ExposedPorts,omitempty"`
	HostConfig       dockerContainerHostConfig    `json:"HostConfig,omitempty"`
	NetworkingConfig dockerNetworkingConfig       `json:"NetworkingConfig,omitempty"`
}

type dockerContainerHostConfig struct {
	Binds         []string                       `json:"Binds,omitempty"`
	NetworkMode   string                         `json:"NetworkMode,omitempty"`
	PortBindings  map[string][]dockerPortBinding `json:"PortBindings,omitempty"`
	RestartPolicy dockerRestartPolicy            `json:"RestartPolicy,omitempty"`
}

type dockerPortBinding struct {
	HostIP   string `json:"HostIp,omitempty"`
	HostPort string `json:"HostPort,omitempty"`
}

type dockerRestartPolicy struct {
	Name string `json:"Name,omitempty"`
}

type dockerNetworkingConfig struct {
	EndpointsConfig map[string]dockerEndpointSettings `json:"EndpointsConfig,omitempty"`
}

type dockerEndpointSettings struct {
	Aliases []string `json:"Aliases,omitempty"`
}

type dockerContainerCreateResponse struct {
	ID string `json:"Id"`
}

type dockerContainerInspect struct {
	ID    string `json:"Id"`
	State struct {
		Running bool `json:"Running"`
	} `json:"State"`
	Config struct {
		Image  string            `json:"Image"`
		Env    []string          `json:"Env"`
		Labels map[string]string `json:"Labels"`
	} `json:"Config"`
}

func (c *dockerContainerClient) CreateContainer(ctx context.Context, name string, request dockerContainerCreateRequest) (string, error) {
	var response dockerContainerCreateResponse
	if err := c.doJSON(ctx, http.MethodPost, "/containers/create?name="+name, request, &response); err != nil {
		return "", err
	}
	if strings.TrimSpace(response.ID) == "" {
		return "", errors.New("docker create container returned empty id")
	}
	return response.ID, nil
}

func (c *dockerContainerClient) StartContainer(ctx context.Context, id string) error {
	return c.doNoContent(ctx, http.MethodPost, "/containers/"+id+"/start")
}

func (c *dockerContainerClient) StopContainer(ctx context.Context, name string, timeoutSeconds int) error {
	return c.doNoContent(ctx, http.MethodPost, fmt.Sprintf("/containers/%s/stop?t=%d", name, timeoutSeconds))
}

func (c *dockerContainerClient) RemoveContainer(ctx context.Context, name string, force bool) error {
	path := fmt.Sprintf("/containers/%s?force=%s", name, strings.ToLower(fmt.Sprintf("%t", force)))
	return c.doNoContent(ctx, http.MethodDelete, path)
}

func (c *dockerContainerClient) InspectContainer(ctx context.Context, name string) (dockerContainerInspect, error) {
	var response dockerContainerInspect
	err := c.doJSON(ctx, http.MethodGet, "/containers/"+name+"/json", nil, &response)
	if err != nil {
		return dockerContainerInspect{}, err
	}
	return response, nil
}

func (c *dockerContainerClient) doNoContent(ctx context.Context, method, path string) error {
	request, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, nil)
	if err != nil {
		return err
	}
	response, err := c.client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode == http.StatusNotFound {
		return errDockerContainerNotFound
	}
	if response.StatusCode >= http.StatusBadRequest {
		body, _ := io.ReadAll(response.Body)
		return fmt.Errorf("docker api %s %s failed: status=%d body=%s", method, path, response.StatusCode, strings.TrimSpace(string(body)))
	}
	return nil
}

func (c *dockerContainerClient) doJSON(ctx context.Context, method, path string, body any, out any) error {
	var reader io.Reader
	if body != nil {
		payload, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(payload)
	}
	request, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, reader)
	if err != nil {
		return err
	}
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	response, err := c.client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode == http.StatusNotFound {
		return errDockerContainerNotFound
	}
	if response.StatusCode >= http.StatusBadRequest {
		payload, _ := io.ReadAll(response.Body)
		return fmt.Errorf("docker api %s %s failed: status=%d body=%s", method, path, response.StatusCode, strings.TrimSpace(string(payload)))
	}
	if out == nil || response.StatusCode == http.StatusNoContent {
		return nil
	}
	return json.NewDecoder(response.Body).Decode(out)
}
