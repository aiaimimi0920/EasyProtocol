package server

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"sort"
	"strings"
	"time"

	"easy_protocol/config"
	"easy_protocol/services"
)

type controlPlaneScope string

const (
	controlPlaneScopeRead   controlPlaneScope = "read"
	controlPlaneScopeMutate controlPlaneScope = "mutate"
	controlPlaneScopePublic controlPlaneScope = "public"
)

func (s *Server) withControlPlaneAccess(scope controlPlaneScope, next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if !s.authorizeControlPlane(w, r, scope) {
			return
		}
		next(w, r)
	}
}

func (s *Server) authorizeControlPlane(w http.ResponseWriter, r *http.Request, scope controlPlaneScope) bool {
	if !s.cfg.ControlPlane.Enabled {
		return true
	}
	s.cleanupExpiredTokenGrace(time.Now().UTC(), r)
	if !s.authorizeControlPlaneNetwork(w, r, scope) {
		return false
	}

	state := s.currentControlPlaneState()
	token := s.controlPlaneToken(r)
	if token == "" {
		s.recordDeniedControlPlane(r, scope, "missing_token", http.StatusUnauthorized)
		writeJSON(w, http.StatusUnauthorized, map[string]any{
			"error":          "missing control-plane token",
			"required_scope": string(scope),
		})
		return false
	}

	now := time.Now().UTC()
	readOK := secureTokenCompare(token, state.ReadToken)
	readGraceOK := graceTokenMatch(token, state.PreviousReadToken, state.ReadTokenGraceUntil, now)
	mutateOK := secureTokenCompare(token, state.MutateToken)
	mutateGraceOK := graceTokenMatch(token, state.PreviousMutateToken, state.MutateTokenGraceUntil, now)

	switch scope {
	case controlPlaneScopeRead:
		if readOK || readGraceOK || mutateOK || mutateGraceOK {
			return true
		}
		s.recordDeniedControlPlane(r, scope, "invalid_token", http.StatusUnauthorized)
		writeJSON(w, http.StatusUnauthorized, map[string]any{
			"error":          "invalid control-plane token",
			"required_scope": string(scope),
		})
		return false
	case controlPlaneScopeMutate:
		if mutateOK || mutateGraceOK {
			if state.FreezeEnabled && !s.controlPlaneBypassPath(r.URL.Path) {
				s.recordDeniedControlPlane(r, scope, "frozen", http.StatusLocked)
				writeJSON(w, http.StatusLocked, map[string]any{
					"error":          "control-plane mutations are frozen",
					"required_scope": string(scope),
				})
				return false
			}
			return true
		}
		if readOK || readGraceOK {
			s.recordDeniedControlPlane(r, scope, "insufficient_scope", http.StatusForbidden)
			writeJSON(w, http.StatusForbidden, map[string]any{
				"error":          "insufficient control-plane scope",
				"required_scope": string(scope),
			})
			return false
		}
		s.recordDeniedControlPlane(r, scope, "invalid_token", http.StatusUnauthorized)
		writeJSON(w, http.StatusUnauthorized, map[string]any{
			"error":          "invalid control-plane token",
			"required_scope": string(scope),
		})
		return false
	default:
		return true
	}
}

func (s *Server) controlPlaneToken(r *http.Request) string {
	if r == nil {
		return ""
	}
	if token := strings.TrimSpace(r.Header.Get("X-EasyProtocol-Token")); token != "" {
		return token
	}
	auth := strings.TrimSpace(r.Header.Get("Authorization"))
	if len(auth) >= len("Bearer ") && strings.EqualFold(auth[:len("Bearer ")], "Bearer ") {
		return strings.TrimSpace(auth[len("Bearer "):])
	}
	return ""
}

func (s *Server) mutationIdentity(w http.ResponseWriter, r *http.Request, actor, reason string) (string, string, bool) {
	actor = s.resolveActor(r, actor)
	reason = s.resolveReason(r, reason)
	if s.cfg.ControlPlane.RequireActor && actor == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"error": "actor is required for mutating control-plane requests",
		})
		return "", "", false
	}
	return actor, reason, true
}

func (s *Server) resolveActor(r *http.Request, actor string) string {
	if r != nil {
		if headerActor := strings.TrimSpace(r.Header.Get("X-EasyProtocol-Actor")); headerActor != "" {
			return headerActor
		}
	}
	return strings.TrimSpace(actor)
}

func (s *Server) resolveReason(r *http.Request, reason string) string {
	if r != nil {
		if headerReason := strings.TrimSpace(r.Header.Get("X-EasyProtocol-Reason")); headerReason != "" {
			return headerReason
		}
	}
	return strings.TrimSpace(reason)
}

func (s *Server) controlPlaneSummary() map[string]any {
	state := s.currentControlPlaneState()
	return map[string]any{
		"enabled":                           s.cfg.ControlPlane.Enabled,
		"require_actor":                     s.cfg.ControlPlane.RequireActor,
		"freeze_enabled":                    state.FreezeEnabled,
		"maintenance_enabled":               state.MaintenanceEnabled,
		"maintenance_reason":                state.MaintenanceReason,
		"maintenance_eta":                   nullableTime(state.MaintenanceETA),
		"maintenance_started_at":            nullableTime(state.MaintenanceStartedAt),
		"maintenance_elapsed_seconds":       maintenanceElapsedSeconds(state),
		"maintenance_eta_remaining_seconds": maintenanceETARemainingSeconds(state),
		"localhost_only":                    s.cfg.ControlPlane.LocalhostOnly,
		"allowlist":                         append([]string(nil), s.cfg.ControlPlane.Allowlist...),
		"token_grace_period_seconds":        int(s.cfg.ControlPlane.TokenGracePeriod / time.Second),
		"read_token_configured":             strings.TrimSpace(state.ReadToken) != "",
		"mutate_token_configured":           strings.TrimSpace(state.MutateToken) != "",
		"read_token_fingerprint":            tokenFingerprint(state.ReadToken),
		"mutate_token_fingerprint":          tokenFingerprint(state.MutateToken),
		"read_token_previous_fingerprint":   tokenFingerprint(state.PreviousReadToken),
		"mutate_token_previous_fingerprint": tokenFingerprint(state.PreviousMutateToken),
		"read_token_grace_until":            nullableTime(state.ReadTokenGraceUntil),
		"mutate_token_grace_until":          nullableTime(state.MutateTokenGraceUntil),
		"last_maintenance_summary":          state.LastMaintenanceSummary,
	}
}

func secureTokenCompare(left, right string) bool {
	if strings.TrimSpace(left) == "" || strings.TrimSpace(right) == "" {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(left), []byte(right)) == 1
}

func decodeOptionalJSON(r *http.Request, out any) error {
	if r == nil || r.Body == nil {
		return nil
	}
	if err := json.NewDecoder(r.Body).Decode(out); err != nil {
		if errors.Is(err, io.EOF) {
			return nil
		}
		return err
	}
	return nil
}

func (s *Server) currentControlPlaneState() services.ControlPlaneState {
	s.controlPlaneMu.RLock()
	defer s.controlPlaneMu.RUnlock()
	return s.controlPlaneState
}

func (s *Server) setControlPlaneState(state services.ControlPlaneState) {
	s.controlPlaneMu.Lock()
	defer s.controlPlaneMu.Unlock()
	s.controlPlaneState = state
}

func (s *Server) persistControlPlaneState() error {
	if s.controlPlaneStore == nil {
		return nil
	}
	return s.controlPlaneStore.Save(s.currentControlPlaneState())
}

func (s *Server) controlPlaneBypassPath(path string) bool {
	trimmed := strings.TrimSpace(path)
	return strings.HasPrefix(trimmed, "/api/internal/control-plane/")
}

func (s *Server) cleanupExpiredTokenGrace(now time.Time, r *http.Request) {
	s.controlPlaneMu.Lock()
	before := s.controlPlaneState
	after := before
	cleanedRead := false
	cleanedMutate := false
	if !after.ReadTokenGraceUntil.IsZero() && now.After(after.ReadTokenGraceUntil.UTC()) {
		after.PreviousReadToken = ""
		after.ReadTokenGraceUntil = time.Time{}
		cleanedRead = true
	}
	if !after.MutateTokenGraceUntil.IsZero() && now.After(after.MutateTokenGraceUntil.UTC()) {
		after.PreviousMutateToken = ""
		after.MutateTokenGraceUntil = time.Time{}
		cleanedMutate = true
	}
	changed := cleanedRead || cleanedMutate
	if changed {
		s.controlPlaneState = after
	}
	s.controlPlaneMu.Unlock()

	if !changed {
		return
	}
	_ = s.persistControlPlaneState()
	if s.auditStore != nil {
		_ = s.auditStore.Record(services.AuditRecord{
			Action:     "control_plane_tokens_grace_cleanup",
			TargetType: "control_plane",
			Target:     "tokens",
			Details: s.withSecurityDetails(r, controlPlaneScopeRead, map[string]any{
				"result":         "succeeded",
				"cleaned_read":   cleanedRead,
				"cleaned_mutate": cleanedMutate,
				"read_before":    nullableTime(before.ReadTokenGraceUntil),
				"mutate_before":  nullableTime(before.MutateTokenGraceUntil),
			}),
		})
	}
}

func (s *Server) currentMaintenanceEnabled() bool {
	return s.currentControlPlaneState().MaintenanceEnabled
}

func (s *Server) withSecurityDetails(r *http.Request, scope controlPlaneScope, extra map[string]any) map[string]any {
	details := map[string]any{}
	for key, value := range extra {
		details[key] = value
	}
	state := s.currentControlPlaneState()
	path := ""
	method := ""
	authorizationPresent := false
	actorHeaderPresent := false
	reasonHeaderPresent := false
	if r != nil {
		path = r.URL.Path
		method = r.Method
		authorizationPresent = strings.TrimSpace(r.Header.Get("Authorization")) != ""
		actorHeaderPresent = strings.TrimSpace(r.Header.Get("X-EasyProtocol-Actor")) != ""
		reasonHeaderPresent = strings.TrimSpace(r.Header.Get("X-EasyProtocol-Reason")) != ""
	}
	details["control_plane"] = map[string]any{
		"path":                         path,
		"method":                       method,
		"scope":                        string(scope),
		"freeze_enabled":               state.FreezeEnabled,
		"maintenance_enabled":          state.MaintenanceEnabled,
		"maintenance_reason":           state.MaintenanceReason,
		"maintenance_eta":              nullableTime(state.MaintenanceETA),
		"maintenance_started_at":       nullableTime(state.MaintenanceStartedAt),
		"require_actor":                s.cfg.ControlPlane.RequireActor,
		"localhost_only":               s.cfg.ControlPlane.LocalhostOnly,
		"allowlist":                    append([]string(nil), s.cfg.ControlPlane.Allowlist...),
		"token_grace_period_seconds":   int(s.cfg.ControlPlane.TokenGracePeriod / time.Second),
		"read_token_grace_until":       nullableTime(state.ReadTokenGraceUntil),
		"mutate_token_grace_until":     nullableTime(state.MutateTokenGraceUntil),
		"client_ip":                    clientIP(r),
		"token_present":                s.controlPlaneToken(r) != "",
		"authorization_header_present": authorizationPresent,
		"actor_header_present":         actorHeaderPresent,
		"reason_header_present":        reasonHeaderPresent,
	}
	return details
}

func (s *Server) recordDeniedControlPlane(r *http.Request, scope controlPlaneScope, outcome string, statusCode int) {
	if s.auditStore == nil {
		return
	}
	_ = s.auditStore.Record(services.AuditRecord{
		Action:     "control_plane_access_denied",
		TargetType: "control_plane",
		Target:     r.URL.Path,
		Actor:      s.resolveActor(r, ""),
		Reason:     s.resolveReason(r, ""),
		Details: s.withSecurityDetails(r, scope, map[string]any{
			"result":      outcome,
			"status_code": statusCode,
		}),
	})
}

func (s *Server) authorizeControlPlaneNetwork(w http.ResponseWriter, r *http.Request, scope controlPlaneScope) bool {
	ip := clientIP(r)
	if ip == "" {
		s.recordDeniedControlPlane(r, scope, "network_restricted", http.StatusForbidden)
		writeJSON(w, http.StatusForbidden, map[string]any{
			"error":          "control-plane network access denied",
			"required_scope": string(scope),
		})
		return false
	}

	if s.cfg.ControlPlane.LocalhostOnly {
		if isLoopbackIP(ip) {
			return true
		}
		s.recordDeniedControlPlane(r, scope, "network_restricted", http.StatusForbidden)
		writeJSON(w, http.StatusForbidden, map[string]any{
			"error":          "control-plane network access denied",
			"required_scope": string(scope),
		})
		return false
	}

	if len(s.cfg.ControlPlane.Allowlist) == 0 {
		return true
	}
	if ipAllowedByList(ip, s.cfg.ControlPlane.Allowlist) {
		return true
	}
	s.recordDeniedControlPlane(r, scope, "network_restricted", http.StatusForbidden)
	writeJSON(w, http.StatusForbidden, map[string]any{
		"error":          "control-plane network access denied",
		"required_scope": string(scope),
	})
	return false
}

func (s *Server) buildMaintenanceCompletionSummary(startedAt, completedAt time.Time, reason string, eta time.Time) *services.MaintenanceCompletionSummary {
	if startedAt.IsZero() || completedAt.IsZero() {
		return nil
	}
	if completedAt.Before(startedAt) {
		completedAt = startedAt
	}
	operationCounts := map[string]int{}
	publicRejectCount := 0
	if s.auditStore != nil {
		for _, record := range s.auditStore.List(services.AuditFilter{}) {
			if record.Action != "public_request_rejected" {
				continue
			}
			createdAt := record.CreatedAt.UTC()
			if createdAt.Before(startedAt.UTC()) || createdAt.After(completedAt.UTC()) {
				continue
			}
			publicRejectCount++
			if operation := auditDetailString(record, "operation"); operation != "" {
				operationCounts[operation]++
			}
		}
	}
	return &services.MaintenanceCompletionSummary{
		StartedAt:             startedAt.UTC(),
		CompletedAt:           completedAt.UTC(),
		DurationSeconds:       int64(completedAt.UTC().Sub(startedAt.UTC()).Seconds()),
		Reason:                strings.TrimSpace(reason),
		ETA:                   eta.UTC(),
		PublicRejectCount:     publicRejectCount,
		TopRejectedOperations: topMaintenanceOperations(operationCounts, 5),
	}
}

func (s *Server) handleControlPlane(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	writeJSON(w, http.StatusOK, s.controlPlaneSummary())
}

func (s *Server) handleControlPlaneDrain(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	writeJSON(w, http.StatusOK, s.controlPlaneDrainSummary())
}

func (s *Server) handleControlPlaneMaintenance(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	var req struct {
		Enabled           *bool  `json:"enabled"`
		Actor             string `json:"actor"`
		Reason            string `json:"reason"`
		MaintenanceReason string `json:"maintenance_reason"`
		MaintenanceETA    string `json:"maintenance_eta"`
	}
	if err := decodeOptionalJSON(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request body"})
		return
	}
	if req.Enabled == nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "enabled is required"})
		return
	}
	actor, reason, ok := s.mutationIdentity(w, r, req.Actor, req.Reason)
	if !ok {
		return
	}
	var maintenanceETA time.Time
	if eta := strings.TrimSpace(req.MaintenanceETA); eta != "" {
		parsed, err := time.Parse(time.RFC3339, eta)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "maintenance_eta must be RFC3339"})
			return
		}
		maintenanceETA = parsed.UTC()
	}
	before := s.currentControlPlaneState()
	after := before
	after.MaintenanceEnabled = *req.Enabled
	if *req.Enabled {
		if after.MaintenanceStartedAt.IsZero() || !before.MaintenanceEnabled {
			after.MaintenanceStartedAt = time.Now().UTC()
		}
		after.MaintenanceReason = strings.TrimSpace(req.MaintenanceReason)
		after.MaintenanceETA = maintenanceETA
	} else {
		if before.MaintenanceEnabled {
			after.LastMaintenanceSummary = s.buildMaintenanceCompletionSummary(before.MaintenanceStartedAt, time.Now().UTC(), before.MaintenanceReason, before.MaintenanceETA)
		}
		after.MaintenanceReason = ""
		after.MaintenanceETA = time.Time{}
		after.MaintenanceStartedAt = time.Time{}
	}
	s.setControlPlaneState(after)
	if err := s.persistControlPlaneState(); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	if s.auditStore != nil {
		_ = s.auditStore.Record(services.AuditRecord{
			Action:     "control_plane_maintenance",
			TargetType: "control_plane",
			Target:     "maintenance",
			Actor:      actor,
			Reason:     reason,
			Details: s.withSecurityDetails(r, controlPlaneScopeMutate, map[string]any{
				"result":                     "succeeded",
				"maintenance_enabled_before": before.MaintenanceEnabled,
				"maintenance_enabled_after":  after.MaintenanceEnabled,
				"maintenance_reason_before":  before.MaintenanceReason,
				"maintenance_reason_after":   after.MaintenanceReason,
				"maintenance_eta_before":     nullableTime(before.MaintenanceETA),
				"maintenance_eta_after":      nullableTime(after.MaintenanceETA),
				"maintenance_started_before": nullableTime(before.MaintenanceStartedAt),
				"maintenance_started_after":  nullableTime(after.MaintenanceStartedAt),
				"completion_summary":         after.LastMaintenanceSummary,
			}),
		})
	}
	writeJSON(w, http.StatusOK, s.controlPlaneSummary())
}

func (s *Server) handleControlPlaneFreeze(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	var req struct {
		Enabled *bool  `json:"enabled"`
		Actor   string `json:"actor"`
		Reason  string `json:"reason"`
	}
	if err := decodeOptionalJSON(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request body"})
		return
	}
	if req.Enabled == nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "enabled is required"})
		return
	}
	actor, reason, ok := s.mutationIdentity(w, r, req.Actor, req.Reason)
	if !ok {
		return
	}
	before := s.currentControlPlaneState()
	after := before
	after.FreezeEnabled = *req.Enabled
	s.setControlPlaneState(after)
	if err := s.persistControlPlaneState(); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	if s.auditStore != nil {
		_ = s.auditStore.Record(services.AuditRecord{
			Action:     "control_plane_freeze",
			TargetType: "control_plane",
			Target:     "freeze",
			Actor:      actor,
			Reason:     reason,
			Details: s.withSecurityDetails(r, controlPlaneScopeMutate, map[string]any{
				"result":                "succeeded",
				"freeze_enabled_before": before.FreezeEnabled,
				"freeze_enabled_after":  after.FreezeEnabled,
			}),
		})
	}
	writeJSON(w, http.StatusOK, s.controlPlaneSummary())
}

func (s *Server) handleControlPlaneTokenRotate(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	var req struct {
		ReadToken          string `json:"read_token"`
		MutateToken        string `json:"mutate_token"`
		GracePeriodSeconds *int   `json:"grace_period_seconds"`
		Actor              string `json:"actor"`
		Reason             string `json:"reason"`
	}
	if err := decodeOptionalJSON(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request body"})
		return
	}
	if strings.TrimSpace(req.ReadToken) == "" && strings.TrimSpace(req.MutateToken) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "read_token or mutate_token is required"})
		return
	}
	actor, reason, ok := s.mutationIdentity(w, r, req.Actor, req.Reason)
	if !ok {
		return
	}
	gracePeriod := s.cfg.ControlPlane.TokenGracePeriod
	if req.GracePeriodSeconds != nil {
		if *req.GracePeriodSeconds < 0 {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "grace_period_seconds must be >= 0"})
			return
		}
		gracePeriod = time.Duration(*req.GracePeriodSeconds) * time.Second
	}
	before := s.currentControlPlaneState()
	after := before
	readUpdated := false
	mutateUpdated := false
	now := time.Now().UTC()
	if token := strings.TrimSpace(req.ReadToken); token != "" {
		if token != before.ReadToken && strings.TrimSpace(before.ReadToken) != "" && gracePeriod > 0 {
			after.PreviousReadToken = before.ReadToken
			after.ReadTokenGraceUntil = now.Add(gracePeriod)
		} else {
			after.PreviousReadToken = ""
			after.ReadTokenGraceUntil = time.Time{}
		}
		after.ReadToken = token
		readUpdated = true
	}
	if token := strings.TrimSpace(req.MutateToken); token != "" {
		if token != before.MutateToken && strings.TrimSpace(before.MutateToken) != "" && gracePeriod > 0 {
			after.PreviousMutateToken = before.MutateToken
			after.MutateTokenGraceUntil = now.Add(gracePeriod)
		} else {
			after.PreviousMutateToken = ""
			after.MutateTokenGraceUntil = time.Time{}
		}
		after.MutateToken = token
		mutateUpdated = true
	}
	if strings.TrimSpace(after.ReadToken) == "" || strings.TrimSpace(after.MutateToken) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "control-plane tokens cannot be empty"})
		return
	}
	s.setControlPlaneState(after)
	if err := s.persistControlPlaneState(); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	if s.auditStore != nil {
		_ = s.auditStore.Record(services.AuditRecord{
			Action:     "control_plane_tokens_rotate",
			TargetType: "control_plane",
			Target:     "tokens",
			Actor:      actor,
			Reason:     reason,
			Details: s.withSecurityDetails(r, controlPlaneScopeMutate, map[string]any{
				"result":                          "succeeded",
				"read_token_updated":              readUpdated,
				"mutate_token_updated":            mutateUpdated,
				"read_token_fingerprint_before":   tokenFingerprint(before.ReadToken),
				"read_token_fingerprint_after":    tokenFingerprint(after.ReadToken),
				"mutate_token_fingerprint_before": tokenFingerprint(before.MutateToken),
				"mutate_token_fingerprint_after":  tokenFingerprint(after.MutateToken),
				"grace_period_seconds":            int(gracePeriod / time.Second),
				"read_token_grace_until":          nullableTime(after.ReadTokenGraceUntil),
				"mutate_token_grace_until":        nullableTime(after.MutateTokenGraceUntil),
			}),
		})
	}
	writeJSON(w, http.StatusOK, s.controlPlaneSummary())
}

func (s *Server) handleControlPlaneSecurityEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	limit := services.ParseAuditLimit(r.URL.Query().Get("limit"))
	if limit <= 0 {
		limit = 100
	}
	writeJSON(w, http.StatusOK, s.controlPlaneSecuritySummary(limit))
}

func (s *Server) controlPlaneDrainSummary() map[string]any {
	state := s.currentControlPlaneState()
	serviceStats := s.stats.All()
	operationStats := s.stats.Operations()
	activeServices := make([]map[string]any, 0)
	activeOperations := make([]map[string]any, 0)
	totalActiveRequests := int64(0)
	for _, item := range serviceStats {
		totalActiveRequests += item.ActiveRequests
		if item.ActiveRequests > 0 {
			activeServices = append(activeServices, map[string]any{
				"service":         item.Service,
				"active_requests": item.ActiveRequests,
			})
		}
	}
	for _, item := range operationStats {
		if item.ActiveRequests > 0 {
			activeOperations = append(activeOperations, map[string]any{
				"operation":             item.Operation,
				"active_requests":       item.ActiveRequests,
				"last_selected_service": item.LastSelectedService,
			})
		}
	}
	return map[string]any{
		"maintenance_enabled":               state.MaintenanceEnabled,
		"maintenance_reason":                state.MaintenanceReason,
		"maintenance_eta":                   nullableTime(state.MaintenanceETA),
		"maintenance_started_at":            nullableTime(state.MaintenanceStartedAt),
		"maintenance_elapsed_seconds":       maintenanceElapsedSeconds(state),
		"maintenance_eta_remaining_seconds": maintenanceETARemainingSeconds(state),
		"last_maintenance_summary":          state.LastMaintenanceSummary,
		"total_active_requests":             totalActiveRequests,
		"active_services":                   activeServices,
		"active_operations":                 activeOperations,
	}
}

func (s *Server) controlPlaneSecuritySummary(limit int) map[string]any {
	recent := s.auditStore.List(services.AuditFilter{Limit: limit})
	securityActions := map[string]struct{}{
		"control_plane_access_denied":        {},
		"control_plane_tokens_rotate":        {},
		"control_plane_tokens_grace_cleanup": {},
		"control_plane_freeze":               {},
		"control_plane_maintenance":          {},
		"public_request_rejected":            {},
	}
	filtered := make([]services.AuditRecord, 0, len(recent))
	byAction := map[string]int{}
	deniedByResult := map[string]int{}
	targetCounts := map[string]int{}
	clientIPCounts := map[string]int{}
	pathCounts := map[string]int{}
	actorCounts := map[string]int{}
	operationCounts := map[string]int{}
	for _, record := range recent {
		if _, ok := securityActions[record.Action]; !ok {
			continue
		}
		filtered = append(filtered, record)
		byAction[record.Action]++
		if record.Target != "" {
			targetCounts[record.Target]++
		}
		if strings.TrimSpace(record.Actor) != "" {
			actorCounts[strings.TrimSpace(record.Actor)]++
		}
		if clientIP := nestedControlPlaneString(record, "client_ip"); clientIP != "" {
			clientIPCounts[clientIP]++
		}
		if path := nestedControlPlaneString(record, "path"); path != "" {
			pathCounts[path]++
		}
		if operation := auditDetailString(record, "operation"); operation != "" {
			operationCounts[operation]++
		}
		if record.Action == "control_plane_access_denied" {
			if result, _ := record.Details["result"].(string); result != "" {
				deniedByResult[result]++
			}
		}
	}
	return map[string]any{
		"total_events":             len(filtered),
		"by_action":                byAction,
		"denied_access_count":      byAction["control_plane_access_denied"],
		"denied_by_result":         deniedByResult,
		"token_rotation_count":     byAction["control_plane_tokens_rotate"],
		"grace_cleanup_count":      byAction["control_plane_tokens_grace_cleanup"],
		"freeze_change_count":      byAction["control_plane_freeze"],
		"maintenance_change_count": byAction["control_plane_maintenance"],
		"public_reject_count":      byAction["public_request_rejected"],
		"top_targets":              topAuditTargets(targetCounts, 5),
		"top_client_ips":           topAuditTargets(clientIPCounts, 5),
		"top_paths":                topAuditTargets(pathCounts, 5),
		"top_actors":               topAuditTargets(actorCounts, 5),
		"top_operations":           topAuditTargets(operationCounts, 5),
		"hourly_trends":            securityTrends(filtered, time.Hour, 24),
		"daily_trends":             securityTrends(filtered, 24*time.Hour, 7),
		"recent_events":            filtered,
	}
}

func (s *Server) recordPublicRequestRejected(r *http.Request, requestID, operation string) {
	if s.auditStore == nil {
		return
	}
	state := s.currentControlPlaneState()
	_ = s.auditStore.Record(services.AuditRecord{
		Action:     "public_request_rejected",
		TargetType: "public_api",
		Target:     "/api/public/request",
		Details: s.withSecurityDetails(r, controlPlaneScopePublic, map[string]any{
			"result":             "maintenance_mode",
			"request_id":         requestID,
			"operation":          operation,
			"maintenance_reason": state.MaintenanceReason,
			"maintenance_eta":    nullableTime(state.MaintenanceETA),
		}),
	})
}

func tokenFingerprint(token string) string {
	trimmed := strings.TrimSpace(token)
	if trimmed == "" {
		return ""
	}
	sum := sha256.Sum256([]byte(trimmed))
	return hex.EncodeToString(sum[:6])
}

func topAuditTargets(counts map[string]int, limit int) []map[string]any {
	type item struct {
		target string
		count  int
	}
	items := make([]item, 0, len(counts))
	for target, count := range counts {
		items = append(items, item{target: target, count: count})
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].count == items[j].count {
			return items[i].target < items[j].target
		}
		return items[i].count > items[j].count
	})
	if limit > 0 && len(items) > limit {
		items = items[:limit]
	}
	out := make([]map[string]any, 0, len(items))
	for _, item := range items {
		out = append(out, map[string]any{
			"target": item.target,
			"count":  item.count,
		})
	}
	return out
}

func topMaintenanceOperations(counts map[string]int, limit int) []services.MaintenanceOperationRejectCount {
	type item struct {
		operation string
		count     int
	}
	items := make([]item, 0, len(counts))
	for operation, count := range counts {
		items = append(items, item{operation: operation, count: count})
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].count == items[j].count {
			return items[i].operation < items[j].operation
		}
		return items[i].count > items[j].count
	})
	if limit > 0 && len(items) > limit {
		items = items[:limit]
	}
	out := make([]services.MaintenanceOperationRejectCount, 0, len(items))
	for _, item := range items {
		out = append(out, services.MaintenanceOperationRejectCount{
			Operation: item.operation,
			Count:     item.count,
		})
	}
	return out
}

func nestedControlPlaneString(record services.AuditRecord, key string) string {
	controlPlane, ok := record.Details["control_plane"].(map[string]any)
	if !ok {
		return ""
	}
	value, _ := controlPlane[key].(string)
	return value
}

func auditDetailString(record services.AuditRecord, key string) string {
	if record.Details == nil {
		return ""
	}
	value, _ := record.Details[key].(string)
	return strings.TrimSpace(value)
}

func securityTrends(records []services.AuditRecord, bucketSize time.Duration, bucketCount int) []map[string]any {
	if bucketCount <= 0 {
		return nil
	}
	now := time.Now().UTC()
	current := truncateTime(now, bucketSize)
	buckets := make([]map[string]any, 0, bucketCount)
	index := make(map[time.Time]map[string]any, bucketCount)
	for step := bucketCount - 1; step >= 0; step-- {
		start := current.Add(-bucketSize * time.Duration(step))
		bucket := map[string]any{
			"bucket_start":             start,
			"total_events":             0,
			"denied_access_count":      0,
			"token_rotation_count":     0,
			"grace_cleanup_count":      0,
			"freeze_change_count":      0,
			"maintenance_change_count": 0,
			"public_reject_count":      0,
		}
		buckets = append(buckets, bucket)
		index[start] = bucket
	}
	for _, record := range records {
		start := truncateTime(record.CreatedAt.UTC(), bucketSize)
		bucket, ok := index[start]
		if !ok {
			continue
		}
		bucket["total_events"] = bucket["total_events"].(int) + 1
		switch record.Action {
		case "control_plane_access_denied":
			bucket["denied_access_count"] = bucket["denied_access_count"].(int) + 1
		case "control_plane_tokens_rotate":
			bucket["token_rotation_count"] = bucket["token_rotation_count"].(int) + 1
		case "control_plane_tokens_grace_cleanup":
			bucket["grace_cleanup_count"] = bucket["grace_cleanup_count"].(int) + 1
		case "control_plane_freeze":
			bucket["freeze_change_count"] = bucket["freeze_change_count"].(int) + 1
		case "control_plane_maintenance":
			bucket["maintenance_change_count"] = bucket["maintenance_change_count"].(int) + 1
		case "public_request_rejected":
			bucket["public_reject_count"] = bucket["public_reject_count"].(int) + 1
		}
	}
	return buckets
}

func truncateTime(value time.Time, bucketSize time.Duration) time.Time {
	if bucketSize >= 24*time.Hour {
		year, month, day := value.Date()
		return time.Date(year, month, day, 0, 0, 0, 0, time.UTC)
	}
	return value.Truncate(bucketSize)
}

func nullableTime(value time.Time) any {
	if value.IsZero() {
		return nil
	}
	return value.UTC()
}

func graceTokenMatch(token, previous string, graceUntil, now time.Time) bool {
	if strings.TrimSpace(previous) == "" || graceUntil.IsZero() || now.After(graceUntil.UTC()) {
		return false
	}
	return secureTokenCompare(token, previous)
}

func maintenanceElapsedSeconds(state services.ControlPlaneState) int64 {
	if !state.MaintenanceEnabled || state.MaintenanceStartedAt.IsZero() {
		return 0
	}
	return int64(time.Since(state.MaintenanceStartedAt.UTC()).Seconds())
}

func maintenanceETARemainingSeconds(state services.ControlPlaneState) int64 {
	if !state.MaintenanceEnabled || state.MaintenanceETA.IsZero() {
		return 0
	}
	remaining := time.Until(state.MaintenanceETA.UTC())
	if remaining < 0 {
		return 0
	}
	return int64(remaining.Seconds())
}

func clientIP(r *http.Request) string {
	if r == nil {
		return ""
	}
	if forwarded := strings.TrimSpace(r.Header.Get("X-Forwarded-For")); forwarded != "" {
		parts := strings.Split(forwarded, ",")
		if len(parts) > 0 {
			if ip := strings.TrimSpace(parts[0]); ip != "" {
				return ip
			}
		}
	}
	host, _, err := net.SplitHostPort(strings.TrimSpace(r.RemoteAddr))
	if err == nil {
		return host
	}
	return strings.TrimSpace(r.RemoteAddr)
}

func isLoopbackIP(raw string) bool {
	ip := net.ParseIP(strings.TrimSpace(raw))
	if ip == nil {
		return false
	}
	return ip.IsLoopback()
}

func ipAllowedByList(raw string, allowlist []string) bool {
	ip := net.ParseIP(strings.TrimSpace(raw))
	if ip == nil {
		return false
	}
	for _, entry := range allowlist {
		item := strings.TrimSpace(entry)
		if item == "" {
			continue
		}
		if exact := net.ParseIP(item); exact != nil {
			if exact.Equal(ip) {
				return true
			}
			continue
		}
		if _, network, err := net.ParseCIDR(item); err == nil && network.Contains(ip) {
			return true
		}
	}
	return false
}

func (s *Server) applyControlPlaneConfig(cfg config.ControlPlaneConfig) {
	s.cfg.ControlPlane = cfg
}
