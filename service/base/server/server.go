package server

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"sort"
	"sync"
	"time"

	"easy_protocol/api"
	"easy_protocol/attribution"
	"easy_protocol/config"
	"easy_protocol/cooling"
	"easy_protocol/registry"
	"easy_protocol/services"
	"easy_protocol/stats"
)

type Server struct {
	cfg               config.Config
	controlPlaneMu    sync.RWMutex
	controlPlaneState services.ControlPlaneState
	registry          *registry.Registry
	refresher         registry.Refresher
	cooling           *cooling.Manager
	attributions      *attribution.Manager
	stats             *stats.Manager
	traceStore        *services.TraceStore
	runtimeStore      *services.RuntimeStateStore
	controlPlaneStore *services.ControlPlaneStateStore
	snapshotStore     *services.RuntimeSnapshotStore
	auditStore        *services.AuditStore
	dispatcher        *services.Dispatcher
	httpServer        *http.Server
}

func New(
	cfg config.Config,
	reg *registry.Registry,
	refresher registry.Refresher,
	coolingMgr *cooling.Manager,
	attributionMgr *attribution.Manager,
	statsMgr *stats.Manager,
	traceStore *services.TraceStore,
	runtimeStore *services.RuntimeStateStore,
	controlPlaneStore *services.ControlPlaneStateStore,
	initialControlPlaneState services.ControlPlaneState,
	snapshotStore *services.RuntimeSnapshotStore,
	auditStore *services.AuditStore,
	dispatcher *services.Dispatcher,
) *Server {
	mux := http.NewServeMux()
	s := &Server{
		cfg:               cfg,
		controlPlaneState: initialControlPlaneState,
		registry:          reg,
		refresher:         refresher,
		cooling:           coolingMgr,
		attributions:      attributionMgr,
		stats:             statsMgr,
		traceStore:        traceStore,
		runtimeStore:      runtimeStore,
		controlPlaneStore: controlPlaneStore,
		snapshotStore:     snapshotStore,
		auditStore:        auditStore,
		dispatcher:        dispatcher,
		httpServer: &http.Server{
			Addr:    cfg.UnifiedAPI.Listen,
			Handler: mux,
		},
	}

	mux.HandleFunc("/api/health", s.handleHealth)
	mux.HandleFunc("/api/public/status", s.handlePublicStatus)
	mux.HandleFunc("/api/public/capabilities", s.handleCapabilities)
	mux.HandleFunc("/api/public/request", s.handleRequest)
	mux.HandleFunc("/api/public/route-preview", s.handleRoutePreview)
	mux.HandleFunc("/api/internal/registry", s.withControlPlaneAccess(controlPlaneScopeRead, s.handleRegistry))
	mux.HandleFunc("/api/internal/registry/refresh", s.withControlPlaneAccess(controlPlaneScopeMutate, s.handleRegistryRefresh))
	mux.HandleFunc("/api/internal/runtime-state", s.withControlPlaneAccess(controlPlaneScopeRead, s.handleRuntimeState))
	mux.HandleFunc("/api/internal/runtime-state/export", s.withControlPlaneAccess(controlPlaneScopeRead, s.handleRuntimeState))
	mux.HandleFunc("/api/internal/runtime-state/import", s.withControlPlaneAccess(controlPlaneScopeMutate, s.handleRuntimeStateImport))
	mux.HandleFunc("/api/internal/runtime-state/snapshots", s.withControlPlaneAccess(controlPlaneScopeRead, s.handleRuntimeStateSnapshots))
	mux.HandleFunc("/api/internal/runtime-state/rollback", s.withControlPlaneAccess(controlPlaneScopeMutate, s.handleRuntimeStateRollback))
	mux.HandleFunc("/api/internal/control-plane", s.withControlPlaneAccess(controlPlaneScopeRead, s.handleControlPlane))
	mux.HandleFunc("/api/internal/control-plane/drain", s.withControlPlaneAccess(controlPlaneScopeRead, s.handleControlPlaneDrain))
	mux.HandleFunc("/api/internal/control-plane/security-events", s.withControlPlaneAccess(controlPlaneScopeRead, s.handleControlPlaneSecurityEvents))
	mux.HandleFunc("/api/internal/control-plane/maintenance", s.withControlPlaneAccess(controlPlaneScopeMutate, s.handleControlPlaneMaintenance))
	mux.HandleFunc("/api/internal/control-plane/freeze", s.withControlPlaneAccess(controlPlaneScopeMutate, s.handleControlPlaneFreeze))
	mux.HandleFunc("/api/internal/control-plane/tokens/rotate", s.withControlPlaneAccess(controlPlaneScopeMutate, s.handleControlPlaneTokenRotate))
	mux.HandleFunc("/api/internal/audit-log", s.withControlPlaneAccess(controlPlaneScopeRead, s.handleAuditLog))
	mux.HandleFunc("/api/internal/audit-log/retention", s.withControlPlaneAccess(controlPlaneScopeRead, s.handleAuditLogRetention))
	mux.HandleFunc("/api/internal/audit-log/prune", s.withControlPlaneAccess(controlPlaneScopeMutate, s.handleAuditLogPrune))
	mux.HandleFunc("/api/internal/policies", s.withControlPlaneAccess(controlPlaneScopeRead, s.handlePolicies))
	mux.HandleFunc("/api/internal/policies/update", s.withControlPlaneAccess(controlPlaneScopeMutate, s.handlePolicyUpdate))
	mux.HandleFunc("/api/internal/services/action", s.withControlPlaneAccess(controlPlaneScopeMutate, s.handleServiceAction))
	mux.HandleFunc("/api/internal/route-preview", s.withControlPlaneAccess(controlPlaneScopeRead, s.handleRoutePreview))
	mux.HandleFunc("/api/internal/route-simulator", s.withControlPlaneAccess(controlPlaneScopeRead, s.handleRouteSimulator))
	mux.HandleFunc("/api/internal/cooling", s.withControlPlaneAccess(controlPlaneScopeRead, s.handleCooling))
	mux.HandleFunc("/api/internal/cooling/reset", s.withControlPlaneAccess(controlPlaneScopeMutate, s.handleCoolingReset))
	mux.HandleFunc("/api/internal/stats", s.withControlPlaneAccess(controlPlaneScopeRead, s.handleStats))
	mux.HandleFunc("/api/internal/stats/reset", s.withControlPlaneAccess(controlPlaneScopeMutate, s.handleStatsReset))
	mux.HandleFunc("/api/internal/errors", s.withControlPlaneAccess(controlPlaneScopeRead, s.handleErrors))
	mux.HandleFunc("/api/internal/errors/clear", s.withControlPlaneAccess(controlPlaneScopeMutate, s.handleErrorsClear))
	mux.HandleFunc("/api/internal/route-traces", s.withControlPlaneAccess(controlPlaneScopeRead, s.handleRouteTraces))
	mux.HandleFunc("/api/internal/route-traces/clear", s.withControlPlaneAccess(controlPlaneScopeMutate, s.handleRouteTracesClear))
	mux.HandleFunc("/api/internal/route-traces/summary", s.withControlPlaneAccess(controlPlaneScopeRead, s.handleRouteTraceSummary))
	mux.HandleFunc("/api/internal/diagnostics/overview", s.withControlPlaneAccess(controlPlaneScopeRead, s.handleDiagnosticsOverview))
	return s
}

func (s *Server) Handler() http.Handler {
	return s.httpServer.Handler
}

func (s *Server) ListenAndServe() error {
	log.Printf("EasyProtocol unified API listening on %s", s.cfg.UnifiedAPI.Listen)
	err := s.httpServer.ListenAndServe()
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}

func (s *Server) Shutdown(ctx context.Context) error {
	return s.httpServer.Shutdown(ctx)
}

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"service": "EasyProtocol",
		"status":  "ok",
		"mode":    s.cfg.Mode,
		"listen":  s.cfg.UnifiedAPI.Listen,
	})
}

func (s *Server) handlePublicStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	state := s.currentControlPlaneState()
	writeJSON(w, http.StatusOK, map[string]any{
		"service":                           "EasyProtocol",
		"available":                         !state.MaintenanceEnabled,
		"maintenance_enabled":               state.MaintenanceEnabled,
		"maintenance_reason":                state.MaintenanceReason,
		"maintenance_eta":                   nullableTime(state.MaintenanceETA),
		"maintenance_started_at":            nullableTime(state.MaintenanceStartedAt),
		"maintenance_elapsed_seconds":       maintenanceElapsedSeconds(state),
		"maintenance_eta_remaining_seconds": maintenanceETARemainingSeconds(state),
		"last_maintenance_summary":          state.LastMaintenanceSummary,
	})
}

func (s *Server) handleCapabilities(w http.ResponseWriter, _ *http.Request) {
	report, err := s.dispatcher.PolicyReport(context.Background())
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	operationPolicies := make(map[string]config.OperationPolicyConfig, len(report.Operations))
	preferredServices := make(map[string][]string, len(report.Operations))
	for _, item := range report.Operations {
		operationPolicies[item.Operation] = item.OperationPolicy
		preferredServices[item.Operation] = append([]string(nil), item.PreferredServices...)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"service":                         "EasyProtocol",
		"strategy_mode":                   report.Global.SelectorMode,
		"fallback_on_retryable_errors":    report.Global.FallbackEnabled,
		"max_fallback_attempts":           report.Global.MaxFallbackAttempts,
		"retryable_categories":            report.Global.RetryableCategories,
		"services":                        s.registry.List(),
		"supported_operations":            buildOperationMatrix(s.registry.List()),
		"preferred_services_by_operation": preferredServices,
		"operation_policies":              operationPolicies,
	})
}

func (s *Server) handleRequest(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	var req api.Request
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request body"})
		return
	}
	if s.currentMaintenanceEnabled() {
		state := s.currentControlPlaneState()
		s.recordPublicRequestRejected(r, req.ID, req.Operation)
		writeJSON(w, http.StatusServiceUnavailable, api.Response{
			RequestID: req.ID,
			Status:    api.StatusFailed,
			Error: func() *attribution.Record {
				record := attribution.NewRecord(req.ID, "", attribution.CategoryMaintenanceMode, "service is in maintenance mode", map[string]any{
					"operation":          req.Operation,
					"maintenance_reason": state.MaintenanceReason,
					"maintenance_eta":    nullableTime(state.MaintenanceETA),
				})
				return &record
			}(),
			Meta: api.ResponseMeta{
				RequestMode: req.Mode,
				RouteReason: "maintenance_mode",
			},
		})
		return
	}

	resp, err := s.dispatcher.Dispatch(r.Context(), req)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}

	status := http.StatusOK
	if resp.Status == api.StatusFailed {
		status = http.StatusBadRequest
	}
	writeJSON(w, status, resp)
}

func (s *Server) handleRegistry(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.registry.List())
}

func (s *Server) handleRegistryRefresh(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	actor, reason, ok := s.mutationIdentity(w, r, "", "")
	if !ok {
		return
	}
	if s.refresher != nil {
		_ = s.refresher.RefreshAll(r.Context())
	}
	if s.auditStore != nil {
		_ = s.auditStore.Record(services.AuditRecord{
			Action:     "registry_refresh",
			TargetType: "registry",
			Actor:      actor,
			Reason:     reason,
			Details:    s.withSecurityDetails(r, controlPlaneScopeMutate, map[string]any{"result": "succeeded"}),
		})
	}
	writeJSON(w, http.StatusOK, s.registry.List())
}

func (s *Server) handleRuntimeState(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	writeJSON(w, http.StatusOK, s.snapshotRuntimeState())
}

func (s *Server) handleRuntimeStateImport(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	var req struct {
		Actor  string                `json:"actor"`
		Reason string                `json:"reason"`
		State  services.RuntimeState `json:"state"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request body"})
		return
	}
	actor, reason, ok := s.mutationIdentity(w, r, req.Actor, req.Reason)
	if !ok {
		return
	}
	req.Actor = actor
	req.Reason = reason
	before := s.snapshotRuntimeState()
	if err := s.applyRuntimeState(req.State); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	after := s.snapshotRuntimeState()
	snapshotID, err := s.persistRuntimeMutation(r, "runtime_state_import", "runtime_state", "", req.Actor, req.Reason, before, after, map[string]any{
		"imported_services": len(after.Services),
	})
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"snapshot_id": snapshotID,
		"state":       after,
	})
}

func (s *Server) handleRuntimeStateSnapshots(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	limit := services.ParseAuditLimit(r.URL.Query().Get("limit"))
	writeJSON(w, http.StatusOK, s.snapshotStore.List(limit))
}

func (s *Server) handleRuntimeStateRollback(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	var req struct {
		SnapshotID string `json:"snapshot_id"`
		Actor      string `json:"actor"`
		Reason     string `json:"reason"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request body"})
		return
	}
	if req.SnapshotID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "snapshot_id is required"})
		return
	}
	actor, reason, ok := s.mutationIdentity(w, r, req.Actor, req.Reason)
	if !ok {
		return
	}
	req.Actor = actor
	req.Reason = reason
	snapshot, err := s.snapshotStore.LoadSnapshot(req.SnapshotID)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "snapshot not found"})
		return
	}
	before := s.snapshotRuntimeState()
	if err := s.applyRuntimeState(snapshot.State); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	after := s.snapshotRuntimeState()
	newSnapshotID, err := s.persistRuntimeMutation(r, "runtime_state_rollback", "runtime_state", "", req.Actor, req.Reason, before, after, map[string]any{
		"rollback_snapshot_id": req.SnapshotID,
	})
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"snapshot_id":         newSnapshotID,
		"rolled_back_to":      req.SnapshotID,
		"restored_state":      after,
		"restored_from_entry": snapshot.Summary(),
	})
}

func (s *Server) handleAuditLog(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	filter := services.AuditFilter{
		Action:     r.URL.Query().Get("action"),
		TargetType: r.URL.Query().Get("target_type"),
		Target:     r.URL.Query().Get("target"),
		Limit:      services.ParseAuditLimit(r.URL.Query().Get("limit")),
	}
	writeJSON(w, http.StatusOK, s.auditStore.List(filter))
}

func (s *Server) handleAuditLogRetention(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	summary := s.auditStore.RetentionSummary()
	writeJSON(w, http.StatusOK, map[string]any{
		"enabled":               summary.Enabled,
		"path":                  summary.Path,
		"limit":                 summary.Limit,
		"record_count":          summary.RecordCount,
		"file_exists":           summary.FileExists,
		"file_size_bytes":       summary.FileSizeBytes,
		"newest_record_at":      nullableTime(summary.NewestRecordAt),
		"oldest_record_at":      nullableTime(summary.OldestRecordAt),
		"history_limit_default": s.cfg.Persistence.AuditHistoryLimit,
	})
}

func (s *Server) handleAuditLogPrune(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	var req struct {
		Keep   *int   `json:"keep"`
		Actor  string `json:"actor"`
		Reason string `json:"reason"`
	}
	if err := decodeOptionalJSON(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request body"})
		return
	}
	actor, reason, ok := s.mutationIdentity(w, r, req.Actor, req.Reason)
	if !ok {
		return
	}
	requestedKeep := s.cfg.Persistence.AuditHistoryLimit
	if req.Keep != nil {
		requestedKeep = *req.Keep
	}
	if requestedKeep < 0 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "keep must be >= 0"})
		return
	}
	before := s.auditStore.RetentionSummary()
	pruneKeep := requestedKeep
	if pruneKeep > 0 {
		pruneKeep--
	}
	pruned, err := s.auditStore.Prune(pruneKeep)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	if s.auditStore != nil {
		_ = s.auditStore.Record(services.AuditRecord{
			Action:     "audit_log_prune",
			TargetType: "audit_log",
			Target:     "retention",
			Actor:      actor,
			Reason:     reason,
			Details: s.withSecurityDetails(r, controlPlaneScopeMutate, map[string]any{
				"result":         "succeeded",
				"requested_keep": requestedKeep,
				"effective_keep": pruned.EffectiveKeep,
				"before_count":   before.RecordCount,
				"after_count":    pruned.AfterCount,
				"pruned_count":   pruned.PrunedCount,
				"before_summary": before,
				"after_summary":  s.auditStore.RetentionSummary(),
			}),
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"requested_keep": requestedKeep,
		"prune":          pruned,
		"retention":      s.auditStore.RetentionSummary(),
	})
}

func (s *Server) handlePolicies(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	report, err := s.dispatcher.PolicyReport(r.Context())
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, report)
}

func (s *Server) handlePolicyUpdate(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	var req services.PolicyUpdateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request body"})
		return
	}
	actor, reason, ok := s.mutationIdentity(w, r, req.Actor, req.Reason)
	if !ok {
		return
	}
	req.Actor = actor
	req.Reason = reason
	before := s.snapshotRuntimeState()
	report, err := s.dispatcher.UpdatePolicies(req)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	after := s.snapshotRuntimeState()
	if _, err := s.persistRuntimeMutation(r, "policy_update", "policy", "", req.Actor, req.Reason, before, after, map[string]any{
		"global_updated":   req.Global != nil,
		"operations_count": len(req.Operations),
	}); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, report)
}

func (s *Server) handleRoutePreview(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	var req api.Request
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request body"})
		return
	}

	preview, err := s.dispatcher.PreviewRoute(r.Context(), req)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, preview)
}

func (s *Server) handleRouteSimulator(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	var req services.RouteSimulationRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request body"})
		return
	}
	result, err := s.dispatcher.SimulateRoute(r.Context(), req)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, result)
}

func (s *Server) handleCooling(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.cooling.All(time.Now().UTC()))
}

func (s *Server) handleCoolingReset(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	var req struct {
		Service string `json:"service"`
		Actor   string `json:"actor"`
		Reason  string `json:"reason"`
	}
	if err := decodeOptionalJSON(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request body"})
		return
	}
	actor, reason, ok := s.mutationIdentity(w, r, req.Actor, req.Reason)
	if !ok {
		return
	}
	req.Actor = actor
	req.Reason = reason
	if req.Service == "" {
		s.cooling.ResetAll()
		_ = s.auditStore.Record(services.AuditRecord{
			Action:     "cooling_reset",
			TargetType: "cooling",
			Actor:      req.Actor,
			Reason:     req.Reason,
			Details:    s.withSecurityDetails(r, controlPlaneScopeMutate, map[string]any{"scope": "all", "result": "succeeded"}),
		})
		writeJSON(w, http.StatusOK, s.cooling.All(time.Now().UTC()))
		return
	}
	s.cooling.Reset(req.Service)
	_ = s.auditStore.Record(services.AuditRecord{
		Action:     "cooling_reset",
		TargetType: "service",
		Target:     req.Service,
		Actor:      req.Actor,
		Reason:     req.Reason,
		Details:    s.withSecurityDetails(r, controlPlaneScopeMutate, map[string]any{"result": "succeeded"}),
	})
	writeJSON(w, http.StatusOK, s.cooling.All(time.Now().UTC()))
}

func (s *Server) handleStats(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"services":           s.stats.All(),
		"operations":         s.stats.Operations(),
		"service_operations": s.stats.ServiceOperations(),
	})
}

func (s *Server) handleStatsReset(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	var req struct {
		Service string `json:"service"`
		Actor   string `json:"actor"`
		Reason  string `json:"reason"`
	}
	if err := decodeOptionalJSON(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request body"})
		return
	}
	actor, reason, ok := s.mutationIdentity(w, r, req.Actor, req.Reason)
	if !ok {
		return
	}
	req.Actor = actor
	req.Reason = reason
	if req.Service == "" {
		s.stats.Reset()
		_ = s.auditStore.Record(services.AuditRecord{
			Action:     "stats_reset",
			TargetType: "stats",
			Actor:      req.Actor,
			Reason:     req.Reason,
			Details:    s.withSecurityDetails(r, controlPlaneScopeMutate, map[string]any{"scope": "all", "result": "succeeded"}),
		})
	} else {
		s.stats.ResetService(req.Service)
		_ = s.auditStore.Record(services.AuditRecord{
			Action:     "stats_reset",
			TargetType: "service",
			Target:     req.Service,
			Actor:      req.Actor,
			Reason:     req.Reason,
			Details:    s.withSecurityDetails(r, controlPlaneScopeMutate, map[string]any{"result": "succeeded"}),
		})
	}
	s.handleStats(w, r)
}

func (s *Server) handleErrors(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.attributions.List())
}

func (s *Server) handleErrorsClear(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	var req struct {
		Actor  string `json:"actor"`
		Reason string `json:"reason"`
	}
	if err := decodeOptionalJSON(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request body"})
		return
	}
	actor, reason, ok := s.mutationIdentity(w, r, req.Actor, req.Reason)
	if !ok {
		return
	}
	req.Actor = actor
	req.Reason = reason
	s.attributions.Clear()
	_ = s.auditStore.Record(services.AuditRecord{
		Action:     "errors_clear",
		TargetType: "errors",
		Actor:      req.Actor,
		Reason:     req.Reason,
		Details:    s.withSecurityDetails(r, controlPlaneScopeMutate, map[string]any{"result": "succeeded"}),
	})
	writeJSON(w, http.StatusOK, s.attributions.List())
}

func (s *Server) handleRouteTraces(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	if requestID := r.URL.Query().Get("request_id"); requestID != "" {
		writeJSON(w, http.StatusOK, s.traceStore.FindByRequestID(requestID))
		return
	}
	writeJSON(w, http.StatusOK, s.traceStore.List())
}

func (s *Server) handleRouteTracesClear(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	var req struct {
		Actor  string `json:"actor"`
		Reason string `json:"reason"`
	}
	if err := decodeOptionalJSON(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request body"})
		return
	}
	actor, reason, ok := s.mutationIdentity(w, r, req.Actor, req.Reason)
	if !ok {
		return
	}
	req.Actor = actor
	req.Reason = reason
	s.traceStore.Clear()
	_ = s.auditStore.Record(services.AuditRecord{
		Action:     "route_traces_clear",
		TargetType: "route_traces",
		Actor:      req.Actor,
		Reason:     req.Reason,
		Details:    s.withSecurityDetails(r, controlPlaneScopeMutate, map[string]any{"result": "succeeded"}),
	})
	writeJSON(w, http.StatusOK, s.traceStore.List())
}

func (s *Server) handleRouteTraceSummary(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	filter := services.TraceFilter{
		Operation:    r.URL.Query().Get("operation"),
		FinalService: r.URL.Query().Get("service"),
		FinalStatus:  r.URL.Query().Get("status"),
	}
	writeJSON(w, http.StatusOK, s.traceStore.Summary(filter))
}

func (s *Server) handleDiagnosticsOverview(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	report, err := s.dispatcher.PolicyReport(r.Context())
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	traceSummary := s.traceStore.Summary(services.TraceFilter{})
	writeJSON(w, http.StatusOK, map[string]any{
		"generated_at":        time.Now().UTC(),
		"control_plane":       s.controlPlaneSummary(),
		"control_plane_drain": s.controlPlaneDrainSummary(),
		"security_events":     s.controlPlaneSecuritySummary(100),
		"audit_retention":     s.auditStore.RetentionSummary(),
		"runtime_state":       s.snapshotRuntimeState(),
		"registry":            s.registry.List(),
		"cooling":             s.cooling.All(time.Now().UTC()),
		"stats": map[string]any{
			"services":           s.stats.All(),
			"operations":         s.stats.Operations(),
			"service_operations": s.stats.ServiceOperations(),
		},
		"trace_summary":          traceSummary,
		"top_failing_operations": topFailingOperations(traceSummary.ByOperation, 5),
		"recent_errors":          s.attributions.List(),
		"recent_audit_log":       s.auditStore.List(services.AuditFilter{Limit: 20}),
		"policies":               report,
	})
}

func (s *Server) handleServiceAction(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	var req struct {
		Service string `json:"service"`
		Action  string `json:"action"`
		Actor   string `json:"actor"`
		Reason  string `json:"reason"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request body"})
		return
	}
	if req.Service == "" || req.Action == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "service and action are required"})
		return
	}
	actor, reason, ok := s.mutationIdentity(w, r, req.Actor, req.Reason)
	if !ok {
		return
	}
	req.Actor = actor
	req.Reason = reason
	action := req.Action
	before := s.snapshotRuntimeState()
	switch action {
	case "enable":
		if _, ok := s.registry.SetEnabled(req.Service, true); !ok {
			writeJSON(w, http.StatusNotFound, map[string]any{"error": "service not found"})
			return
		}
	case "disable":
		if _, ok := s.registry.SetEnabled(req.Service, false); !ok {
			writeJSON(w, http.StatusNotFound, map[string]any{"error": "service not found"})
			return
		}
	case "refresh":
		if s.refresher == nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "refresher is not configured"})
			return
		}
		if err := s.refresher.RefreshService(r.Context(), req.Service); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
	case "reset_cooling":
		s.cooling.Reset(req.Service)
	case "reset_stats":
		s.stats.ResetService(req.Service)
	case "reset_health":
		if _, ok := s.registry.ResetHealth(req.Service); !ok {
			writeJSON(w, http.StatusNotFound, map[string]any{"error": "service not found"})
			return
		}
	default:
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "unsupported action"})
		return
	}
	if action == "enable" || action == "disable" {
		after := s.snapshotRuntimeState()
		if _, err := s.persistRuntimeMutation(r, "service_action", "service", req.Service, req.Actor, req.Reason, before, after, map[string]any{
			"service_action": action,
			"service":        req.Service,
		}); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
			return
		}
	} else if s.auditStore != nil {
		_ = s.auditStore.Record(services.AuditRecord{
			Action:     "service_action",
			TargetType: "service",
			Target:     req.Service,
			Actor:      req.Actor,
			Reason:     req.Reason,
			Details: s.withSecurityDetails(r, controlPlaneScopeMutate, map[string]any{
				"service_action": action,
				"result":         "succeeded",
			}),
		})
	}
	service, ok := s.registry.Get(req.Service)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "service not found"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"service": service,
		"cooling": s.cooling.Snapshot(req.Service, time.Now().UTC()),
		"stats":   s.stats.Snapshot(req.Service),
	})
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func buildOperationMatrix(services []registry.Service) map[string][]string {
	matrix := map[string][]string{}
	for _, service := range services {
		for _, operation := range service.SupportedOperations {
			matrix[operation] = append(matrix[operation], service.Name)
		}
	}
	for operation := range matrix {
		sort.Strings(matrix[operation])
	}
	return matrix
}

func topFailingOperations(items []services.OperationTraceSummary, limit int) []services.OperationTraceSummary {
	out := append([]services.OperationTraceSummary(nil), items...)
	sort.Slice(out, func(i, j int) bool {
		if out[i].FailureCount == out[j].FailureCount {
			return out[i].Operation < out[j].Operation
		}
		return out[i].FailureCount > out[j].FailureCount
	})
	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out
}

func (s *Server) snapshotRuntimeState() services.RuntimeState {
	state := services.RuntimeState{
		Policy:   s.dispatcher.SnapshotRuntimePolicyState(),
		Services: make(map[string]services.RuntimeServiceState),
	}
	for _, service := range s.registry.List() {
		state.Services[service.Name] = services.RuntimeServiceState{
			Enabled: service.Enabled,
		}
	}
	return state
}

func (s *Server) persistRuntimeState() error {
	if s.runtimeStore == nil {
		return nil
	}
	return s.runtimeStore.Save(s.snapshotRuntimeState())
}

func (s *Server) applyRuntimeState(state services.RuntimeState) error {
	s.dispatcher.ApplyRuntimePolicyState(state.Policy)
	for serviceName, item := range state.Services {
		if _, ok := s.registry.SetEnabled(serviceName, item.Enabled); !ok {
			return errors.New("service not found in runtime state: " + serviceName)
		}
	}
	return s.persistRuntimeState()
}

func (s *Server) persistRuntimeMutation(r *http.Request, action, targetType, target, actor, reason string, before, after services.RuntimeState, details map[string]any) (string, error) {
	if details == nil {
		details = map[string]any{}
	}
	details["diff"] = services.DiffRuntimeStates(before, after)
	details = s.withSecurityDetails(r, controlPlaneScopeMutate, details)

	var snapshotID string
	if s.snapshotStore != nil && s.snapshotStore.Enabled() {
		summary, err := s.snapshotStore.Save(action, actor, reason, after)
		if err != nil {
			return "", err
		}
		snapshotID = summary.ID
		details["snapshot_id"] = snapshotID
	}
	if err := s.persistRuntimeState(); err != nil {
		return "", err
	}
	if s.auditStore != nil {
		if err := s.auditStore.Record(services.AuditRecord{
			Action:     action,
			TargetType: targetType,
			Target:     target,
			Actor:      actor,
			Reason:     reason,
			Details:    details,
		}); err != nil {
			return "", err
		}
	}
	return snapshotID, nil
}
