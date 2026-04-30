package server

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"

	"easy_protocol/api"
	"easy_protocol/attribution"
	"easy_protocol/config"
	"easy_protocol/cooling"
	"easy_protocol/registry"
	"easy_protocol/services"
	"easy_protocol/stats"
	"easy_protocol/transports"
)

type runtimeStateTestFixture struct {
	server            *Server
	runtimeStore      *services.RuntimeStateStore
	controlPlaneStore *services.ControlPlaneStateStore
	snapshotStore     *services.RuntimeSnapshotStore
	auditStore        *services.AuditStore
	registry          *registry.Registry
	dispatcher        *services.Dispatcher
	stats             *stats.Manager
	readToken         string
	mutateToken       string
}

func TestRuntimeStateImportExportAndAudit(t *testing.T) {
	fixture := newRuntimeStateTestFixture(t)

	importState := services.RuntimeState{
		Policy: services.RuntimePolicyState{
			Global: services.RuntimeGlobalPolicyState{
				FallbackOnRetryableErrors: false,
				MaxFallbackAttempts:       1,
				RetryableCategories:       []string{"service_unavailable"},
			},
			Operations: map[string]services.RuntimeOperationPolicyState{
				"protocol.query.encode": {
					PreferredServices: []string{"GolangProtocol", "JSProtocol"},
					Policy: config.OperationPolicyConfig{
						FallbackMode:        "disabled",
						MaxFallbackAttempts: 1,
					},
				},
			},
		},
		Services: map[string]services.RuntimeServiceState{
			"JSProtocol": {Enabled: false},
		},
	}

	response := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodPost, "/api/internal/runtime-state/import", fixture.mutateHeaders("tester"), map[string]any{
		"actor":  "tester",
		"reason": "import for test",
		"state":  importState,
	})
	if response.Code != http.StatusOK {
		t.Fatalf("expected 200 from import, got %d: %s", response.Code, response.Body.String())
	}

	var importPayload struct {
		SnapshotID string                `json:"snapshot_id"`
		State      services.RuntimeState `json:"state"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &importPayload); err != nil {
		t.Fatalf("decode import payload failed: %v", err)
	}
	if importPayload.SnapshotID == "" {
		t.Fatalf("expected snapshot_id in import response")
	}
	if importPayload.State.Services["JSProtocol"].Enabled {
		t.Fatalf("expected imported JSProtocol state to be disabled, got %#v", importPayload.State.Services["JSProtocol"])
	}

	exportResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/runtime-state/export", fixture.readHeaders(), nil)
	if exportResponse.Code != http.StatusOK {
		t.Fatalf("expected 200 from export, got %d: %s", exportResponse.Code, exportResponse.Body.String())
	}

	var exported services.RuntimeState
	if err := json.Unmarshal(exportResponse.Body.Bytes(), &exported); err != nil {
		t.Fatalf("decode export payload failed: %v", err)
	}
	if exported.Policy.Global.FallbackOnRetryableErrors {
		t.Fatalf("expected fallback disabled after import, got %#v", exported.Policy.Global)
	}
	if exported.Policy.Operations["protocol.query.encode"].PreferredServices[0] != "GolangProtocol" {
		t.Fatalf("expected preferred services override to be exported, got %#v", exported.Policy.Operations["protocol.query.encode"])
	}

	persisted, err := fixture.runtimeStore.Load()
	if err != nil {
		t.Fatalf("load persisted runtime state failed: %v", err)
	}
	if persisted.Services["JSProtocol"].Enabled {
		t.Fatalf("expected persisted JSProtocol to be disabled, got %#v", persisted.Services["JSProtocol"])
	}

	service, ok := fixture.registry.Get("JSProtocol")
	if !ok {
		t.Fatalf("expected JSProtocol to exist in registry")
	}
	if service.Enabled {
		t.Fatalf("expected registry JSProtocol to be disabled after import, got %#v", service)
	}

	auditResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/audit-log?action=runtime_state_import&limit=1", fixture.readHeaders(), nil)
	if auditResponse.Code != http.StatusOK {
		t.Fatalf("expected 200 from audit log, got %d: %s", auditResponse.Code, auditResponse.Body.String())
	}
	var records []services.AuditRecord
	if err := json.Unmarshal(auditResponse.Body.Bytes(), &records); err != nil {
		t.Fatalf("decode audit response failed: %v", err)
	}
	if len(records) != 1 {
		t.Fatalf("expected one audit record, got %#v", records)
	}
	if records[0].Action != "runtime_state_import" {
		t.Fatalf("expected runtime_state_import audit record, got %#v", records[0])
	}
	diff, ok := records[0].Details["diff"].(map[string]any)
	if !ok || len(diff) == 0 {
		t.Fatalf("expected audit diff details, got %#v", records[0].Details)
	}
	if _, ok := records[0].Details["snapshot_id"]; !ok {
		t.Fatalf("expected audit record to include snapshot_id, got %#v", records[0].Details)
	}

	snapshotResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/runtime-state/snapshots?limit=1", fixture.readHeaders(), nil)
	if snapshotResponse.Code != http.StatusOK {
		t.Fatalf("expected 200 from snapshots, got %d: %s", snapshotResponse.Code, snapshotResponse.Body.String())
	}
	var snapshots []services.RuntimeSnapshotSummary
	if err := json.Unmarshal(snapshotResponse.Body.Bytes(), &snapshots); err != nil {
		t.Fatalf("decode snapshots failed: %v", err)
	}
	if len(snapshots) != 1 || snapshots[0].Action != "runtime_state_import" {
		t.Fatalf("expected latest snapshot to be runtime_state_import, got %#v", snapshots)
	}
}

func TestRuntimeStateRollbackRestoresSnapshot(t *testing.T) {
	fixture := newRuntimeStateTestFixture(t)

	stateA := services.RuntimeState{
		Policy: services.RuntimePolicyState{
			Global: services.RuntimeGlobalPolicyState{
				FallbackOnRetryableErrors: false,
				MaxFallbackAttempts:       1,
				RetryableCategories:       []string{"service_unavailable"},
			},
			Operations: map[string]services.RuntimeOperationPolicyState{
				"protocol.query.encode": {
					PreferredServices: []string{"GolangProtocol", "JSProtocol"},
					Policy: config.OperationPolicyConfig{
						FallbackMode:        "disabled",
						MaxFallbackAttempts: 1,
					},
				},
			},
		},
		Services: map[string]services.RuntimeServiceState{
			"JSProtocol": {Enabled: false},
		},
	}
	stateB := services.RuntimeState{
		Policy: services.RuntimePolicyState{
			Global: services.RuntimeGlobalPolicyState{
				FallbackOnRetryableErrors: true,
				MaxFallbackAttempts:       2,
				RetryableCategories:       []string{"service_unavailable", "transport_error"},
			},
			Operations: map[string]services.RuntimeOperationPolicyState{
				"protocol.query.encode": {
					PreferredServices: []string{"JSProtocol", "GolangProtocol"},
					Policy: config.OperationPolicyConfig{
						FallbackMode:        "enabled",
						MaxFallbackAttempts: 2,
					},
				},
			},
		},
		Services: map[string]services.RuntimeServiceState{
			"JSProtocol": {Enabled: true},
		},
	}

	var payloadA struct {
		SnapshotID string `json:"snapshot_id"`
	}
	respA := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodPost, "/api/internal/runtime-state/import", fixture.mutateHeaders("tester"), map[string]any{
		"actor":  "tester",
		"reason": "state a",
		"state":  stateA,
	})
	if respA.Code != http.StatusOK {
		t.Fatalf("expected import A to succeed, got %d: %s", respA.Code, respA.Body.String())
	}
	if err := json.Unmarshal(respA.Body.Bytes(), &payloadA); err != nil {
		t.Fatalf("decode import A failed: %v", err)
	}

	respB := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodPost, "/api/internal/runtime-state/import", fixture.mutateHeaders("tester"), map[string]any{
		"actor":  "tester",
		"reason": "state b",
		"state":  stateB,
	})
	if respB.Code != http.StatusOK {
		t.Fatalf("expected import B to succeed, got %d: %s", respB.Code, respB.Body.String())
	}

	rollbackResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodPost, "/api/internal/runtime-state/rollback", fixture.mutateHeaders("tester"), map[string]any{
		"snapshot_id": payloadA.SnapshotID,
		"actor":       "tester",
		"reason":      "rollback to A",
	})
	if rollbackResponse.Code != http.StatusOK {
		t.Fatalf("expected rollback to succeed, got %d: %s", rollbackResponse.Code, rollbackResponse.Body.String())
	}

	var rollbackPayload struct {
		SnapshotID    string                          `json:"snapshot_id"`
		RolledBackTo  string                          `json:"rolled_back_to"`
		RestoredState services.RuntimeState           `json:"restored_state"`
		RestoredFrom  services.RuntimeSnapshotSummary `json:"restored_from_entry"`
	}
	if err := json.Unmarshal(rollbackResponse.Body.Bytes(), &rollbackPayload); err != nil {
		t.Fatalf("decode rollback response failed: %v", err)
	}
	if rollbackPayload.RolledBackTo != payloadA.SnapshotID {
		t.Fatalf("expected rollback target %q, got %#v", payloadA.SnapshotID, rollbackPayload)
	}
	if rollbackPayload.RestoredState.Services["JSProtocol"].Enabled {
		t.Fatalf("expected rollback to restore JSProtocol disabled state, got %#v", rollbackPayload.RestoredState.Services["JSProtocol"])
	}
	if rollbackPayload.RestoredState.Policy.Operations["protocol.query.encode"].PreferredServices[0] != "GolangProtocol" {
		t.Fatalf("expected rollback to restore GolangProtocol preference, got %#v", rollbackPayload.RestoredState.Policy.Operations["protocol.query.encode"])
	}

	exportResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/runtime-state", fixture.readHeaders(), nil)
	if exportResponse.Code != http.StatusOK {
		t.Fatalf("expected runtime-state export after rollback, got %d: %s", exportResponse.Code, exportResponse.Body.String())
	}
	var exported services.RuntimeState
	if err := json.Unmarshal(exportResponse.Body.Bytes(), &exported); err != nil {
		t.Fatalf("decode runtime state after rollback failed: %v", err)
	}
	if exported.Services["JSProtocol"].Enabled {
		t.Fatalf("expected exported runtime state to show JS disabled after rollback, got %#v", exported.Services["JSProtocol"])
	}

	auditResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/audit-log?action=runtime_state_rollback&limit=1", fixture.readHeaders(), nil)
	if auditResponse.Code != http.StatusOK {
		t.Fatalf("expected rollback audit response, got %d: %s", auditResponse.Code, auditResponse.Body.String())
	}
	var records []services.AuditRecord
	if err := json.Unmarshal(auditResponse.Body.Bytes(), &records); err != nil {
		t.Fatalf("decode rollback audit response failed: %v", err)
	}
	if len(records) != 1 {
		t.Fatalf("expected one rollback audit record, got %#v", records)
	}
	if records[0].Details["rollback_snapshot_id"] != payloadA.SnapshotID {
		t.Fatalf("expected rollback_snapshot_id detail, got %#v", records[0].Details)
	}

	snapshotsResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/runtime-state/snapshots?limit=5", fixture.readHeaders(), nil)
	if snapshotsResponse.Code != http.StatusOK {
		t.Fatalf("expected snapshots response after rollback, got %d: %s", snapshotsResponse.Code, snapshotsResponse.Body.String())
	}
	var snapshots []services.RuntimeSnapshotSummary
	if err := json.Unmarshal(snapshotsResponse.Body.Bytes(), &snapshots); err != nil {
		t.Fatalf("decode snapshots response failed: %v", err)
	}
	if len(snapshots) < 3 {
		t.Fatalf("expected rollback to create a third snapshot entry, got %#v", snapshots)
	}
	if snapshots[0].Action != "runtime_state_rollback" {
		t.Fatalf("expected most recent snapshot to be rollback, got %#v", snapshots[0])
	}
}

func TestInternalControlPlaneAuthAndActorRequirements(t *testing.T) {
	fixture := newRuntimeStateTestFixture(t)

	noTokenResponse := performJSONRequest(t, fixture.server.Handler(), http.MethodGet, "/api/internal/policies", nil)
	if noTokenResponse.Code != http.StatusUnauthorized {
		t.Fatalf("expected missing token to return 401, got %d: %s", noTokenResponse.Code, noTokenResponse.Body.String())
	}

	readAllowedResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/policies", fixture.readHeaders(), nil)
	if readAllowedResponse.Code != http.StatusOK {
		t.Fatalf("expected read token to access policies, got %d: %s", readAllowedResponse.Code, readAllowedResponse.Body.String())
	}

	readOnMutateResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodPost, "/api/internal/policies/update", fixture.readHeaders(), map[string]any{
		"operations": []map[string]any{
			{
				"operation":          "protocol.query.encode",
				"preferred_services": []string{"GolangProtocol", "JSProtocol"},
			},
		},
	})
	if readOnMutateResponse.Code != http.StatusForbidden {
		t.Fatalf("expected read token on mutating endpoint to return 403, got %d: %s", readOnMutateResponse.Code, readOnMutateResponse.Body.String())
	}

	mutateWithoutActor := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodPost, "/api/internal/policies/update", fixture.mutateHeaders(""), map[string]any{
		"operations": []map[string]any{
			{
				"operation":          "protocol.query.encode",
				"preferred_services": []string{"GolangProtocol", "JSProtocol"},
			},
		},
	})
	if mutateWithoutActor.Code != http.StatusBadRequest {
		t.Fatalf("expected missing actor on mutating endpoint to return 400, got %d: %s", mutateWithoutActor.Code, mutateWithoutActor.Body.String())
	}

	authorizedMutation := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodPost, "/api/internal/policies/update", fixture.mutateHeaders("security-tester"), map[string]any{
		"reason": "security test",
		"operations": []map[string]any{
			{
				"operation":          "protocol.query.encode",
				"preferred_services": []string{"GolangProtocol", "JSProtocol"},
			},
		},
	})
	if authorizedMutation.Code != http.StatusOK {
		t.Fatalf("expected mutate token with actor to succeed, got %d: %s", authorizedMutation.Code, authorizedMutation.Body.String())
	}

	previewResponse := performJSONRequest(t, fixture.server.Handler(), http.MethodPost, "/api/public/route-preview", map[string]any{
		"operation": "protocol.query.encode",
	})
	if previewResponse.Code != http.StatusOK {
		t.Fatalf("expected public preview to remain unauthenticated, got %d: %s", previewResponse.Code, previewResponse.Body.String())
	}

	auditResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/audit-log?action=policy_update&limit=1", fixture.readHeaders(), nil)
	if auditResponse.Code != http.StatusOK {
		t.Fatalf("expected audit log query to succeed, got %d: %s", auditResponse.Code, auditResponse.Body.String())
	}
	var records []services.AuditRecord
	if err := json.Unmarshal(auditResponse.Body.Bytes(), &records); err != nil {
		t.Fatalf("decode audit records failed: %v", err)
	}
	if len(records) != 1 {
		t.Fatalf("expected one policy_update audit record, got %#v", records)
	}
	if records[0].Actor != "security-tester" {
		t.Fatalf("expected audit actor to come from control-plane identity, got %#v", records[0])
	}
}

func TestControlPlaneTokenRotationAndFreezePersistence(t *testing.T) {
	fixture := newRuntimeStateTestFixture(t)

	newReadToken := "read-rotated"
	newMutateToken := "mutate-rotated"
	rotateResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodPost, "/api/internal/control-plane/tokens/rotate", fixture.mutateHeaders("security-admin"), map[string]any{
		"actor":                "security-admin",
		"reason":               "rotate tokens",
		"read_token":           newReadToken,
		"mutate_token":         newMutateToken,
		"grace_period_seconds": 60,
	})
	if rotateResponse.Code != http.StatusOK {
		t.Fatalf("expected token rotation to succeed, got %d: %s", rotateResponse.Code, rotateResponse.Body.String())
	}

	oldReadResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/policies", fixture.readHeaders(), nil)
	if oldReadResponse.Code != http.StatusOK {
		t.Fatalf("expected old read token to be accepted during grace period, got %d: %s", oldReadResponse.Code, oldReadResponse.Body.String())
	}

	newReadHeaders := map[string]string{"X-EasyProtocol-Token": newReadToken}
	newMutateHeaders := map[string]string{
		"X-EasyProtocol-Token":  newMutateToken,
		"X-EasyProtocol-Actor":  "security-admin",
		"X-EasyProtocol-Reason": "freeze control-plane",
	}

	newReadResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/policies", newReadHeaders, nil)
	if newReadResponse.Code != http.StatusOK {
		t.Fatalf("expected rotated read token to access policies, got %d: %s", newReadResponse.Code, newReadResponse.Body.String())
	}

	freezeResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodPost, "/api/internal/control-plane/freeze", newMutateHeaders, map[string]any{
		"enabled": true,
		"actor":   "security-admin",
		"reason":  "freeze mutations",
	})
	if freezeResponse.Code != http.StatusOK {
		t.Fatalf("expected freeze to succeed, got %d: %s", freezeResponse.Code, freezeResponse.Body.String())
	}

	frozenMutation := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodPost, "/api/internal/policies/update", newMutateHeaders, map[string]any{
		"operations": []map[string]any{
			{
				"operation":          "protocol.query.encode",
				"preferred_services": []string{"GolangProtocol", "JSProtocol"},
			},
		},
	})
	if frozenMutation.Code != http.StatusLocked {
		t.Fatalf("expected frozen control-plane to block mutations with 423, got %d: %s", frozenMutation.Code, frozenMutation.Body.String())
	}

	unfreezeResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodPost, "/api/internal/control-plane/freeze", newMutateHeaders, map[string]any{
		"enabled": false,
		"actor":   "security-admin",
		"reason":  "unfreeze mutations",
	})
	if unfreezeResponse.Code != http.StatusOK {
		t.Fatalf("expected unfreeze to succeed, got %d: %s", unfreezeResponse.Code, unfreezeResponse.Body.String())
	}

	persisted, err := fixture.controlPlaneStore.Load()
	if err != nil {
		t.Fatalf("expected persisted control-plane state, got error: %v", err)
	}
	if persisted.ReadToken != newReadToken || persisted.MutateToken != newMutateToken || persisted.FreezeEnabled {
		t.Fatalf("unexpected persisted control-plane state: %#v", persisted)
	}
	if persisted.PreviousReadToken != fixture.readToken || persisted.PreviousMutateToken != fixture.mutateToken {
		t.Fatalf("expected previous tokens to be persisted for grace period, got %#v", persisted)
	}
	if persisted.ReadTokenGraceUntil.IsZero() || persisted.MutateTokenGraceUntil.IsZero() {
		t.Fatalf("expected grace deadlines to be persisted, got %#v", persisted)
	}

	auditResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/audit-log?action=control_plane_tokens_rotate&limit=1", newReadHeaders, nil)
	if auditResponse.Code != http.StatusOK {
		t.Fatalf("expected rotated token to read audit log, got %d: %s", auditResponse.Code, auditResponse.Body.String())
	}
	var records []services.AuditRecord
	if err := json.Unmarshal(auditResponse.Body.Bytes(), &records); err != nil {
		t.Fatalf("decode rotation audit failed: %v", err)
	}
	if len(records) != 1 {
		t.Fatalf("expected one control_plane_tokens_rotate record, got %#v", records)
	}
	if records[0].Actor != "security-admin" {
		t.Fatalf("expected rotated audit actor, got %#v", records[0])
	}
}

func TestControlPlaneLocalhostOnlyAndAllowlist(t *testing.T) {
	localhostFixture := newRuntimeStateTestFixtureWithConfig(t, func(cfg *config.Config) {
		cfg.ControlPlane.LocalhostOnly = true
	})

	deniedRemote := performJSONRequestWithRemote(t, localhostFixture.server.Handler(), http.MethodGet, "/api/internal/policies", localhostFixture.readHeaders(), nil, "203.0.113.10:4000")
	if deniedRemote.Code != http.StatusForbidden {
		t.Fatalf("expected localhost_only to deny remote client, got %d: %s", deniedRemote.Code, deniedRemote.Body.String())
	}

	allowedLocal := performJSONRequestWithRemote(t, localhostFixture.server.Handler(), http.MethodGet, "/api/internal/policies", localhostFixture.readHeaders(), nil, "127.0.0.1:4000")
	if allowedLocal.Code != http.StatusOK {
		t.Fatalf("expected localhost_only to allow loopback client, got %d: %s", allowedLocal.Code, allowedLocal.Body.String())
	}

	allowlistFixture := newRuntimeStateTestFixtureWithConfig(t, func(cfg *config.Config) {
		cfg.ControlPlane.Allowlist = []string{"203.0.113.0/24"}
	})

	allowedCIDR := performJSONRequestWithRemote(t, allowlistFixture.server.Handler(), http.MethodGet, "/api/internal/policies", allowlistFixture.readHeaders(), nil, "203.0.113.25:5000")
	if allowedCIDR.Code != http.StatusOK {
		t.Fatalf("expected allowlist to allow CIDR member, got %d: %s", allowedCIDR.Code, allowedCIDR.Body.String())
	}

	deniedCIDR := performJSONRequestWithRemote(t, allowlistFixture.server.Handler(), http.MethodGet, "/api/internal/policies", allowlistFixture.readHeaders(), nil, "198.51.100.25:5000")
	if deniedCIDR.Code != http.StatusForbidden {
		t.Fatalf("expected allowlist to deny non-member, got %d: %s", deniedCIDR.Code, deniedCIDR.Body.String())
	}
}

func TestMaintenanceModeBlocksPublicRequestAndSecuritySummary(t *testing.T) {
	fixture := newRuntimeStateTestFixture(t)

	maintenanceOn := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodPost, "/api/internal/control-plane/maintenance", fixture.mutateHeaders("ops-admin"), map[string]any{
		"enabled":            true,
		"actor":              "ops-admin",
		"reason":             "planned maintenance",
		"maintenance_reason": "deploy window",
		"maintenance_eta":    "2026-04-01T10:30:00Z",
	})
	if maintenanceOn.Code != http.StatusOK {
		t.Fatalf("expected maintenance enable to succeed, got %d: %s", maintenanceOn.Code, maintenanceOn.Body.String())
	}

	publicRequest := performJSONRequest(t, fixture.server.Handler(), http.MethodPost, "/api/public/request", map[string]any{
		"request_id": "maint-req-1",
		"mode":       "strategy",
		"operation":  "protocol.query.encode",
		"payload": map[string]any{
			"params": map[string]any{"a": 1},
		},
	})
	if publicRequest.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected maintenance mode to return 503, got %d: %s", publicRequest.Code, publicRequest.Body.String())
	}
	var failed api.Response
	if err := json.Unmarshal(publicRequest.Body.Bytes(), &failed); err != nil {
		t.Fatalf("decode maintenance failure failed: %v", err)
	}
	if failed.Error == nil || failed.Error.Category != attribution.CategoryMaintenanceMode {
		t.Fatalf("expected maintenance_mode error, got %#v", failed)
	}
	if failed.Error.Details["maintenance_reason"] != "deploy window" {
		t.Fatalf("expected maintenance reason in public failure details, got %#v", failed.Error.Details)
	}

	publicPreview := performJSONRequest(t, fixture.server.Handler(), http.MethodPost, "/api/public/route-preview", map[string]any{
		"operation": "protocol.query.encode",
	})
	if publicPreview.Code != http.StatusOK {
		t.Fatalf("expected public route preview to remain available during maintenance, got %d: %s", publicPreview.Code, publicPreview.Body.String())
	}

	securitySummary := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/control-plane/security-events?limit=20", fixture.readHeaders(), nil)
	if securitySummary.Code != http.StatusOK {
		t.Fatalf("expected security summary to succeed, got %d: %s", securitySummary.Code, securitySummary.Body.String())
	}
	var summary struct {
		PublicRejectCount      int              `json:"public_reject_count"`
		MaintenanceChangeCount int              `json:"maintenance_change_count"`
		TopOperations          []map[string]any `json:"top_operations"`
	}
	if err := json.Unmarshal(securitySummary.Body.Bytes(), &summary); err != nil {
		t.Fatalf("decode security summary failed: %v", err)
	}
	if summary.PublicRejectCount == 0 || summary.MaintenanceChangeCount == 0 {
		t.Fatalf("expected maintenance and public rejection counts in security summary, got %#v", summary)
	}
	if len(summary.TopOperations) == 0 {
		t.Fatalf("expected top_operations in security summary, got %#v", summary)
	}

	drain := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/control-plane/drain", fixture.readHeaders(), nil)
	if drain.Code != http.StatusOK {
		t.Fatalf("expected drain summary to succeed, got %d: %s", drain.Code, drain.Body.String())
	}
	var drainPayload struct {
		MaintenanceEnabled bool `json:"maintenance_enabled"`
	}
	if err := json.Unmarshal(drain.Body.Bytes(), &drainPayload); err != nil {
		t.Fatalf("decode drain summary failed: %v", err)
	}
	if !drainPayload.MaintenanceEnabled {
		t.Fatalf("expected drain summary to reflect maintenance enabled, got %#v", drainPayload)
	}

	publicStatus := performJSONRequest(t, fixture.server.Handler(), http.MethodGet, "/api/public/status", nil)
	if publicStatus.Code != http.StatusOK {
		t.Fatalf("expected public status to succeed, got %d: %s", publicStatus.Code, publicStatus.Body.String())
	}
	var publicStatusPayload struct {
		Available         bool   `json:"available"`
		MaintenanceReason string `json:"maintenance_reason"`
	}
	if err := json.Unmarshal(publicStatus.Body.Bytes(), &publicStatusPayload); err != nil {
		t.Fatalf("decode public status failed: %v", err)
	}
	if publicStatusPayload.Available {
		t.Fatalf("expected public status to report unavailable during maintenance, got %#v", publicStatusPayload)
	}
	if publicStatusPayload.MaintenanceReason != "deploy window" {
		t.Fatalf("expected public status to expose maintenance reason, got %#v", publicStatusPayload)
	}

	overview := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/diagnostics/overview", fixture.readHeaders(), nil)
	if overview.Code != http.StatusOK {
		t.Fatalf("expected diagnostics overview to succeed, got %d: %s", overview.Code, overview.Body.String())
	}
	var overviewPayload struct {
		ControlPlane map[string]any `json:"control_plane"`
	}
	if err := json.Unmarshal(overview.Body.Bytes(), &overviewPayload); err != nil {
		t.Fatalf("decode diagnostics overview failed: %v", err)
	}
	if enabled, _ := overviewPayload.ControlPlane["maintenance_enabled"].(bool); !enabled {
		t.Fatalf("expected diagnostics overview to report maintenance enabled, got %#v", overviewPayload.ControlPlane)
	}
	if reason, _ := overviewPayload.ControlPlane["maintenance_reason"].(string); reason != "deploy window" {
		t.Fatalf("expected diagnostics overview to expose maintenance reason, got %#v", overviewPayload.ControlPlane)
	}
}

func TestMaintenanceCompletionSummaryPersistsAfterDisable(t *testing.T) {
	fixture := newRuntimeStateTestFixture(t)

	enableResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodPost, "/api/internal/control-plane/maintenance", fixture.mutateHeaders("ops-admin"), map[string]any{
		"enabled":            true,
		"actor":              "ops-admin",
		"reason":             "start maintenance",
		"maintenance_reason": "cutover window",
		"maintenance_eta":    "2026-04-01T11:00:00Z",
	})
	if enableResponse.Code != http.StatusOK {
		t.Fatalf("expected maintenance enable to succeed, got %d: %s", enableResponse.Code, enableResponse.Body.String())
	}

	for _, operation := range []string{"protocol.query.encode", "protocol.regex.extract"} {
		response := performJSONRequest(t, fixture.server.Handler(), http.MethodPost, "/api/public/request", map[string]any{
			"request_id": "maintenance-summary-" + operation,
			"mode":       "strategy",
			"operation":  operation,
		})
		if response.Code != http.StatusServiceUnavailable {
			t.Fatalf("expected maintenance public request rejection for %s, got %d: %s", operation, response.Code, response.Body.String())
		}
	}

	disableResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodPost, "/api/internal/control-plane/maintenance", fixture.mutateHeaders("ops-admin"), map[string]any{
		"enabled": false,
		"actor":   "ops-admin",
		"reason":  "finish maintenance",
	})
	if disableResponse.Code != http.StatusOK {
		t.Fatalf("expected maintenance disable to succeed, got %d: %s", disableResponse.Code, disableResponse.Body.String())
	}

	controlPlane := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/control-plane", fixture.readHeaders(), nil)
	if controlPlane.Code != http.StatusOK {
		t.Fatalf("expected control-plane summary to succeed, got %d: %s", controlPlane.Code, controlPlane.Body.String())
	}
	var controlPlanePayload map[string]any
	if err := json.Unmarshal(controlPlane.Body.Bytes(), &controlPlanePayload); err != nil {
		t.Fatalf("decode control-plane summary failed: %v", err)
	}
	lastSummary, ok := controlPlanePayload["last_maintenance_summary"].(map[string]any)
	if !ok {
		t.Fatalf("expected last_maintenance_summary in control-plane summary, got %#v", controlPlanePayload)
	}
	if rejects, _ := lastSummary["public_reject_count"].(float64); int(rejects) != 2 {
		t.Fatalf("expected two rejected public requests in last maintenance summary, got %#v", lastSummary)
	}
	if reason, _ := lastSummary["reason"].(string); reason != "cutover window" {
		t.Fatalf("expected last maintenance reason to persist, got %#v", lastSummary)
	}

	ops, ok := lastSummary["top_rejected_operations"].([]any)
	if !ok || len(ops) == 0 {
		t.Fatalf("expected top_rejected_operations in last maintenance summary, got %#v", lastSummary)
	}

	publicStatus := performJSONRequest(t, fixture.server.Handler(), http.MethodGet, "/api/public/status", nil)
	if publicStatus.Code != http.StatusOK {
		t.Fatalf("expected public status to succeed, got %d: %s", publicStatus.Code, publicStatus.Body.String())
	}
	var publicStatusPayload map[string]any
	if err := json.Unmarshal(publicStatus.Body.Bytes(), &publicStatusPayload); err != nil {
		t.Fatalf("decode public status failed: %v", err)
	}
	if available, _ := publicStatusPayload["available"].(bool); !available {
		t.Fatalf("expected public status to report available after maintenance disable, got %#v", publicStatusPayload)
	}
	if _, ok := publicStatusPayload["last_maintenance_summary"].(map[string]any); !ok {
		t.Fatalf("expected public status to expose last_maintenance_summary, got %#v", publicStatusPayload)
	}

	persisted, err := fixture.controlPlaneStore.Load()
	if err != nil {
		t.Fatalf("expected persisted control-plane state, got error: %v", err)
	}
	if persisted.LastMaintenanceSummary == nil || persisted.LastMaintenanceSummary.PublicRejectCount != 2 {
		t.Fatalf("expected persisted maintenance completion summary, got %#v", persisted)
	}
}

func TestAuditLogRetentionAndPrune(t *testing.T) {
	fixture := newRuntimeStateTestFixture(t)

	for _, record := range []services.AuditRecord{
		{Action: "policy_update", TargetType: "policy", Target: "protocol.query.encode"},
		{Action: "service_action", TargetType: "service", Target: "JSProtocol"},
		{Action: "control_plane_freeze", TargetType: "control_plane", Target: "freeze"},
	} {
		if err := fixture.auditStore.Record(record); err != nil {
			t.Fatalf("seed audit log failed: %v", err)
		}
	}

	retentionBefore := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/audit-log/retention", fixture.readHeaders(), nil)
	if retentionBefore.Code != http.StatusOK {
		t.Fatalf("expected audit retention summary to succeed, got %d: %s", retentionBefore.Code, retentionBefore.Body.String())
	}
	var beforePayload map[string]any
	if err := json.Unmarshal(retentionBefore.Body.Bytes(), &beforePayload); err != nil {
		t.Fatalf("decode audit retention summary failed: %v", err)
	}
	if count, _ := beforePayload["record_count"].(float64); int(count) < 3 {
		t.Fatalf("expected seeded audit records before prune, got %#v", beforePayload)
	}

	pruneResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodPost, "/api/internal/audit-log/prune", fixture.mutateHeaders("audit-admin"), map[string]any{
		"keep":   2,
		"actor":  "audit-admin",
		"reason": "trim audit history",
	})
	if pruneResponse.Code != http.StatusOK {
		t.Fatalf("expected audit prune to succeed, got %d: %s", pruneResponse.Code, pruneResponse.Body.String())
	}
	var prunePayload struct {
		RequestedKeep int `json:"requested_keep"`
		Retention     struct {
			RecordCount int `json:"record_count"`
		} `json:"retention"`
	}
	if err := json.Unmarshal(pruneResponse.Body.Bytes(), &prunePayload); err != nil {
		t.Fatalf("decode audit prune payload failed: %v", err)
	}
	if prunePayload.RequestedKeep != 2 {
		t.Fatalf("expected requested_keep=2, got %#v", prunePayload)
	}
	if prunePayload.Retention.RecordCount != 2 {
		t.Fatalf("expected final retained audit count to be 2, got %#v", prunePayload)
	}

	auditResponse := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/audit-log?action=audit_log_prune&limit=1", fixture.readHeaders(), nil)
	if auditResponse.Code != http.StatusOK {
		t.Fatalf("expected prune audit query to succeed, got %d: %s", auditResponse.Code, auditResponse.Body.String())
	}
	var records []services.AuditRecord
	if err := json.Unmarshal(auditResponse.Body.Bytes(), &records); err != nil {
		t.Fatalf("decode prune audit records failed: %v", err)
	}
	if len(records) != 1 {
		t.Fatalf("expected one audit_log_prune record, got %#v", records)
	}
	if records[0].Actor != "audit-admin" {
		t.Fatalf("expected prune audit actor to be persisted, got %#v", records[0])
	}
}

func TestControlPlaneDrainSummaryAndSecurityTrends(t *testing.T) {
	fixture := newRuntimeStateTestFixture(t)

	fixture.stats.Begin("GolangProtocol", "protocol.query.encode")
	fixture.stats.Begin("JSProtocol", "protocol.regex.extract")

	drain := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/control-plane/drain", fixture.readHeaders(), nil)
	if drain.Code != http.StatusOK {
		t.Fatalf("expected drain summary to succeed, got %d: %s", drain.Code, drain.Body.String())
	}
	var drainPayload struct {
		TotalActiveRequests int64            `json:"total_active_requests"`
		ActiveServices      []map[string]any `json:"active_services"`
		ActiveOperations    []map[string]any `json:"active_operations"`
	}
	if err := json.Unmarshal(drain.Body.Bytes(), &drainPayload); err != nil {
		t.Fatalf("decode drain payload failed: %v", err)
	}
	if drainPayload.TotalActiveRequests != 2 {
		t.Fatalf("expected total active requests to be 2, got %#v", drainPayload)
	}
	if len(drainPayload.ActiveServices) != 2 || len(drainPayload.ActiveOperations) != 2 {
		t.Fatalf("expected active services and operations in drain payload, got %#v", drainPayload)
	}

	_ = performJSONRequest(t, fixture.server.Handler(), http.MethodGet, "/api/internal/policies", nil)
	_ = performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodPost, "/api/internal/control-plane/maintenance", fixture.mutateHeaders("trend-admin"), map[string]any{
		"enabled":            true,
		"actor":              "trend-admin",
		"reason":             "maintenance for trend test",
		"maintenance_reason": "trend test",
	})
	_ = performJSONRequest(t, fixture.server.Handler(), http.MethodPost, "/api/public/request", map[string]any{
		"request_id": "trend-request-1",
		"operation":  "protocol.query.encode",
	})

	securitySummary := performJSONRequestWithHeaders(t, fixture.server.Handler(), http.MethodGet, "/api/internal/control-plane/security-events?limit=50", fixture.readHeaders(), nil)
	if securitySummary.Code != http.StatusOK {
		t.Fatalf("expected security summary to succeed, got %d: %s", securitySummary.Code, securitySummary.Body.String())
	}
	var summary struct {
		HourlyTrends []map[string]any `json:"hourly_trends"`
		DailyTrends  []map[string]any `json:"daily_trends"`
	}
	if err := json.Unmarshal(securitySummary.Body.Bytes(), &summary); err != nil {
		t.Fatalf("decode security trend summary failed: %v", err)
	}
	if len(summary.HourlyTrends) == 0 || len(summary.DailyTrends) == 0 {
		t.Fatalf("expected hourly and daily security trends, got %#v", summary)
	}
}

func newRuntimeStateTestFixture(t *testing.T) runtimeStateTestFixture {
	return newRuntimeStateTestFixtureWithConfig(t, nil)
}

func newRuntimeStateTestFixtureWithConfig(t *testing.T, mutate func(*config.Config)) runtimeStateTestFixture {
	t.Helper()

	cfg := config.DefaultConfig()
	cfg.Normalize()
	tempDir := t.TempDir()
	cfg.Persistence.Enabled = true
	cfg.Persistence.RuntimeStatePath = filepath.Join(tempDir, "state", "runtime-overrides.json")
	cfg.Persistence.ControlPlaneStatePath = filepath.Join(tempDir, "state", "control-plane-state.json")
	cfg.Persistence.AuditLogPath = filepath.Join(tempDir, "state", "audit-log.jsonl")
	cfg.Persistence.SnapshotDir = filepath.Join(tempDir, "state", "runtime-snapshots")
	cfg.Persistence.AuditHistoryLimit = 50
	cfg.Persistence.SnapshotLimit = 50
	cfg.ControlPlane.Enabled = true
	cfg.ControlPlane.ReadToken = "read-secret"
	cfg.ControlPlane.MutateToken = "mutate-secret"
	cfg.ControlPlane.RequireActor = true
	if mutate != nil {
		mutate(&cfg)
		cfg.Normalize()
	}

	reg := registry.New()
	reg.Register(registry.NewService("GolangProtocol", "go", "http://127.0.0.1:11001", true, []string{"protocol.query.encode"}))
	reg.Register(registry.NewService("JSProtocol", "javascript", "http://127.0.0.1:11002", true, []string{"protocol.query.encode"}))
	reg.Register(registry.NewService("PythonProtocol", "python", "http://127.0.0.1:11003", true, []string{"protocol.regex.extract"}))

	coolingMgr := cooling.New(3, time.Hour)
	attributionMgr := attribution.NewManager(20)
	statsMgr := stats.New()
	traceStore := services.NewTraceStore(true, 50)
	runtimeStore := services.NewRuntimeStateStore(true, cfg.Persistence.RuntimeStatePath)
	controlPlaneStore := services.NewControlPlaneStateStore(true, cfg.Persistence.ControlPlaneStatePath)
	snapshotStore := services.NewRuntimeSnapshotStore(true, cfg.Persistence.SnapshotDir, cfg.Persistence.SnapshotLimit)
	auditStore := services.NewAuditStore(true, cfg.Persistence.AuditLogPath, cfg.Persistence.AuditHistoryLimit)

	if err := snapshotStore.Load(); err != nil {
		t.Fatalf("load empty snapshot store failed: %v", err)
	}
	if err := auditStore.Load(); err != nil {
		t.Fatalf("load empty audit store failed: %v", err)
	}

	dispatcher := services.NewDispatcher(
		cfg,
		reg,
		nil,
		coolingMgr,
		attributionMgr,
		statsMgr,
		traceStore,
		transports.StubTransport{},
	)

	initialControlPlaneState := services.ControlPlaneState{
		ReadToken:   cfg.ControlPlane.ReadToken,
		MutateToken: cfg.ControlPlane.MutateToken,
	}
	srv := New(cfg, reg, nil, coolingMgr, attributionMgr, statsMgr, traceStore, runtimeStore, controlPlaneStore, initialControlPlaneState, snapshotStore, auditStore, dispatcher)
	return runtimeStateTestFixture{
		server:            srv,
		runtimeStore:      runtimeStore,
		controlPlaneStore: controlPlaneStore,
		snapshotStore:     snapshotStore,
		auditStore:        auditStore,
		registry:          reg,
		dispatcher:        dispatcher,
		stats:             statsMgr,
		readToken:         cfg.ControlPlane.ReadToken,
		mutateToken:       cfg.ControlPlane.MutateToken,
	}
}

func performJSONRequest(t *testing.T, handler http.Handler, method, path string, body any) *httptest.ResponseRecorder {
	return performJSONRequestWithRemote(t, handler, method, path, nil, body, "")
}

func performJSONRequestWithHeaders(t *testing.T, handler http.Handler, method, path string, headers map[string]string, body any) *httptest.ResponseRecorder {
	return performJSONRequestWithRemote(t, handler, method, path, headers, body, "")
}

func performJSONRequestWithRemote(t *testing.T, handler http.Handler, method, path string, headers map[string]string, body any, remoteAddr string) *httptest.ResponseRecorder {
	t.Helper()

	var payload []byte
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			t.Fatalf("marshal request body failed: %v", err)
		}
		payload = data
	}

	request := httptest.NewRequest(method, path, bytes.NewReader(payload))
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	for key, value := range headers {
		request.Header.Set(key, value)
	}
	if remoteAddr != "" {
		request.RemoteAddr = remoteAddr
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request.WithContext(context.Background()))
	return response
}

func (f runtimeStateTestFixture) readHeaders() map[string]string {
	return map[string]string{
		"X-EasyProtocol-Token": f.readToken,
	}
}

func (f runtimeStateTestFixture) mutateHeaders(actor string) map[string]string {
	headers := map[string]string{
		"X-EasyProtocol-Token": f.mutateToken,
	}
	if actor != "" {
		headers["X-EasyProtocol-Actor"] = actor
	}
	return headers
}
