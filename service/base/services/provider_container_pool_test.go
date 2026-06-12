package services

import (
	"context"
	"errors"
	"net/http"
	"testing"

	"easy_protocol/registry"
)

func TestProviderContainerPoolAdoptRemovesManagedChildWithMismatchedImage(t *testing.T) {
	originalHealthWaiter := providerChildHealthWaiter
	originalCapabilitiesFetcher := providerChildCapabilitiesFetcher
	t.Cleanup(func() {
		providerChildHealthWaiter = originalHealthWaiter
		providerChildCapabilitiesFetcher = originalCapabilitiesFetcher
	})
	providerChildHealthWaiter = func(_ context.Context, _ *http.Client, _ string) error {
		return nil
	}
	providerChildCapabilitiesFetcher = func(_ context.Context, _ *http.Client, _ string) ([]string, error) {
		return []string{"codex.semantic.step"}, nil
	}

	docker := &fakeProviderContainerDocker{
		inspects: map[string]dockerContainerInspect{
			"easy-protocol-python-001": {
				ID: "old-container",
				State: struct {
					Running bool `json:"Running"`
				}{Running: true},
				Config: struct {
					Image  string            `json:"Image"`
					Env    []string          `json:"Env"`
					Labels map[string]string `json:"Labels"`
				}{
					Image: "ghcr.io/test/easy-protocol-python:old",
					Env:   []string{"EASY_PROTOCOL_CHILD_SERVICE_NAME=PythonProtocol-001"},
					Labels: map[string]string{
						"easyprotocol.managed_child": "true",
					},
				},
			},
		},
	}
	family := &providerContainerFamily{
		providerID:          "python",
		language:            "python",
		servicePrefix:       "PythonProtocol",
		containerNamePrefix: "easy-protocol-python",
		endpointHostPrefix:  "easy-protocol-python",
		image:               "ghcr.io/test/easy-protocol-python:new",
		port:                9100,
		supportedOps:        []string{"codex.semantic.step"},
		maxReplicas:         1,
		nextReplicaIndex:    1,
		children:            map[string]*providerContainerChild{},
	}
	pool := &providerContainerPool{
		registry:   registry.New(),
		docker:     docker,
		httpClient: &http.Client{},
	}

	if err := pool.adoptExistingChildren(context.Background(), family); err != nil {
		t.Fatalf("adopt existing children: %v", err)
	}

	if len(family.children) != 0 {
		t.Fatalf("expected mismatched child not to be adopted, got %d child", len(family.children))
	}
	if len(docker.removed) != 1 || docker.removed[0] != "easy-protocol-python-001" {
		t.Fatalf("expected old managed child to be removed, got %#v", docker.removed)
	}
}

func TestProviderContainerPoolAdoptRemovesManagedChildWithMismatchedEnvironment(t *testing.T) {
	originalHealthWaiter := providerChildHealthWaiter
	originalCapabilitiesFetcher := providerChildCapabilitiesFetcher
	t.Cleanup(func() {
		providerChildHealthWaiter = originalHealthWaiter
		providerChildCapabilitiesFetcher = originalCapabilitiesFetcher
	})
	providerChildHealthWaiter = func(_ context.Context, _ *http.Client, _ string) error {
		return nil
	}
	providerChildCapabilitiesFetcher = func(_ context.Context, _ *http.Client, _ string) ([]string, error) {
		return []string{"codex.semantic.step"}, nil
	}

	docker := &fakeProviderContainerDocker{
		inspects: map[string]dockerContainerInspect{
			"easy-protocol-python-001": {
				ID: "old-container",
				State: struct {
					Running bool `json:"Running"`
				}{Running: true},
				Config: struct {
					Image  string            `json:"Image"`
					Env    []string          `json:"Env"`
					Labels map[string]string `json:"Labels"`
				}{
					Image: "ghcr.io/test/easy-protocol-python:new",
					Env: []string{
						"MAILBOX_SERVICE_API_KEY=mailbox-old",
						"EASY_PROXY_API_KEY=proxy-old",
						"EASY_PROTOCOL_CHILD_SERVICE_NAME=PythonProtocol-001",
					},
					Labels: map[string]string{
						"easyprotocol.managed_child": "true",
					},
				},
			},
		},
	}
	family := &providerContainerFamily{
		providerID:          "python",
		language:            "python",
		servicePrefix:       "PythonProtocol",
		containerNamePrefix: "easy-protocol-python",
		endpointHostPrefix:  "easy-protocol-python",
		image:               "ghcr.io/test/easy-protocol-python:new",
		port:                9100,
		environment: map[string]string{
			"MAILBOX_SERVICE_API_KEY": "mailbox-new",
			"EASY_PROXY_API_KEY":      "proxy-new",
		},
		supportedOps:     []string{"codex.semantic.step"},
		maxReplicas:      1,
		nextReplicaIndex: 1,
		children:         map[string]*providerContainerChild{},
	}
	pool := &providerContainerPool{
		registry:   registry.New(),
		docker:     docker,
		httpClient: &http.Client{},
	}

	if err := pool.adoptExistingChildren(context.Background(), family); err != nil {
		t.Fatalf("adopt existing children: %v", err)
	}

	if len(family.children) != 0 {
		t.Fatalf("expected env-mismatched child not to be adopted, got %d child", len(family.children))
	}
	if len(docker.removed) != 1 || docker.removed[0] != "easy-protocol-python-001" {
		t.Fatalf("expected env-mismatched managed child to be removed, got %#v", docker.removed)
	}
}

func TestProviderContainerPoolSpawnAdoptsConflictingManagedChild(t *testing.T) {
	originalHealthWaiter := providerChildHealthWaiter
	originalCapabilitiesFetcher := providerChildCapabilitiesFetcher
	t.Cleanup(func() {
		providerChildHealthWaiter = originalHealthWaiter
		providerChildCapabilitiesFetcher = originalCapabilitiesFetcher
	})
	providerChildHealthWaiter = func(_ context.Context, _ *http.Client, _ string) error {
		return nil
	}
	providerChildCapabilitiesFetcher = func(_ context.Context, _ *http.Client, _ string) ([]string, error) {
		return []string{"codex.semantic.step"}, nil
	}

	docker := &fakeProviderContainerDocker{
		createErr: errors.New(`docker api POST /containers/create?name=easy-protocol-python-001 failed: status=409 body={"message":"Conflict. The container name \"/easy-protocol-python-001\" is already in use"}`),
		inspects: map[string]dockerContainerInspect{
			"easy-protocol-python-001": {
				ID: "existing-container",
				State: struct {
					Running bool `json:"Running"`
				}{Running: true},
				Config: struct {
					Image  string            `json:"Image"`
					Env    []string          `json:"Env"`
					Labels map[string]string `json:"Labels"`
				}{
					Image: "ghcr.io/test/easy-protocol-python:new",
					Env:   []string{"EASY_PROTOCOL_CHILD_SERVICE_NAME=PythonProtocol-001"},
					Labels: map[string]string{
						"easyprotocol.managed_child": "true",
					},
				},
			},
		},
	}
	family := &providerContainerFamily{
		providerID:          "python",
		language:            "python",
		servicePrefix:       "PythonProtocol",
		containerNamePrefix: "easy-protocol-python",
		endpointHostPrefix:  "easy-protocol-python",
		image:               "ghcr.io/test/easy-protocol-python:new",
		port:                9100,
		supportedOps:        []string{"codex.semantic.step"},
		maxReplicas:         1,
		nextReplicaIndex:    1,
		children:            map[string]*providerContainerChild{},
	}
	pool := &providerContainerPool{
		registry:   registry.New(),
		docker:     docker,
		httpClient: &http.Client{},
	}

	child, err := pool.spawnChild(context.Background(), family)
	if err != nil {
		t.Fatalf("spawn should adopt existing managed child on docker name conflict: %v", err)
	}

	if child.containerID != "existing-container" {
		t.Fatalf("expected adopted container id, got %q", child.containerID)
	}
	if len(family.children) != 1 {
		t.Fatalf("expected adopted child to be registered, got %d", len(family.children))
	}
	if docker.started != 0 {
		t.Fatalf("expected no start call for already running child, got %d", docker.started)
	}
}

type fakeProviderContainerDocker struct {
	inspects  map[string]dockerContainerInspect
	removed   []string
	createErr error
	started   int
}

func (f *fakeProviderContainerDocker) CreateContainer(_ context.Context, _ string, _ dockerContainerCreateRequest) (string, error) {
	if f.createErr != nil {
		return "", f.createErr
	}
	return "", nil
}

func (f *fakeProviderContainerDocker) StartContainer(_ context.Context, _ string) error {
	f.started++
	return nil
}

func (f *fakeProviderContainerDocker) StopContainer(_ context.Context, _ string, _ int) error {
	return nil
}

func (f *fakeProviderContainerDocker) RemoveContainer(_ context.Context, name string, _ bool) error {
	f.removed = append(f.removed, name)
	return nil
}

func (f *fakeProviderContainerDocker) InspectContainer(_ context.Context, name string) (dockerContainerInspect, error) {
	inspect, ok := f.inspects[name]
	if !ok {
		return dockerContainerInspect{}, errDockerContainerNotFound
	}
	return inspect, nil
}
