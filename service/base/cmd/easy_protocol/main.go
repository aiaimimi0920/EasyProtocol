package main

import (
	"context"
	"log"
	"os"
	"strings"

	"easy_protocol/attribution"
	"easy_protocol/config"
	"easy_protocol/cooling"
	"easy_protocol/registry"
	"easy_protocol/server"
	"easy_protocol/services"
	"easy_protocol/stats"
	"easy_protocol/transports"
)

func main() {
	cfg, err := config.Load(strings.TrimSpace(os.Getenv("EASY_PROTOCOL_CONFIG_PATH")))
	if err != nil {
		log.Fatalf("failed to load easy protocol config: %v", err)
	}

	reg := registry.New()
	for _, service := range cfg.Services {
		reg.Register(registry.NewService(service.Name, service.Language, service.Endpoint, service.Enabled, service.SupportedOperations))
	}
	refresher := registry.NewHTTPRefresher(reg)
	_ = refresher.RefreshAll(context.Background())

	coolingMgr := cooling.New(cfg.Strategy.FailureThreshold, cfg.Strategy.CooldownDuration)
	attributionMgr := attribution.NewManager(100)
	statsMgr := stats.New()
	traceStore := services.NewTraceStore(cfg.Tracing.Enabled, cfg.Tracing.HistoryLimit)
	runtimeStore := services.NewRuntimeStateStore(cfg.Persistence.Enabled, cfg.Persistence.RuntimeStatePath)
	controlPlaneStore := services.NewControlPlaneStateStore(cfg.Persistence.Enabled, cfg.Persistence.ControlPlaneStatePath)
	snapshotStore := services.NewRuntimeSnapshotStore(cfg.Persistence.Enabled, cfg.Persistence.SnapshotDir, cfg.Persistence.SnapshotLimit)
	auditStore := services.NewAuditStore(cfg.Persistence.Enabled, cfg.Persistence.AuditLogPath, cfg.Persistence.AuditHistoryLimit)
	if err := snapshotStore.Load(); err != nil {
		log.Fatalf("failed to load runtime snapshots: %v", err)
	}
	if err := auditStore.Load(); err != nil {
		log.Fatalf("failed to load audit log: %v", err)
	}
	initialControlPlaneState := services.ControlPlaneState{
		ReadToken:   cfg.ControlPlane.ReadToken,
		MutateToken: cfg.ControlPlane.MutateToken,
	}
	if state, err := controlPlaneStore.Load(); err != nil {
		log.Fatalf("failed to load control-plane state: %v", err)
	} else if !state.IsZero() {
		if state.ReadToken != "" {
			initialControlPlaneState.ReadToken = state.ReadToken
		}
		if state.MutateToken != "" {
			initialControlPlaneState.MutateToken = state.MutateToken
		}
		initialControlPlaneState.PreviousReadToken = state.PreviousReadToken
		initialControlPlaneState.PreviousMutateToken = state.PreviousMutateToken
		initialControlPlaneState.ReadTokenGraceUntil = state.ReadTokenGraceUntil
		initialControlPlaneState.MutateTokenGraceUntil = state.MutateTokenGraceUntil
		initialControlPlaneState.FreezeEnabled = state.FreezeEnabled
		initialControlPlaneState.MaintenanceEnabled = state.MaintenanceEnabled
		initialControlPlaneState.MaintenanceReason = state.MaintenanceReason
		initialControlPlaneState.MaintenanceETA = state.MaintenanceETA
		initialControlPlaneState.MaintenanceStartedAt = state.MaintenanceStartedAt
		initialControlPlaneState.LastMaintenanceSummary = state.LastMaintenanceSummary
	}
	transport := transports.NewHTTPTransport(reg)
	dispatcher := services.NewDispatcher(cfg, reg, refresher, coolingMgr, attributionMgr, statsMgr, traceStore, transport)
	if state, err := runtimeStore.Load(); err != nil {
		log.Fatalf("failed to load runtime state: %v", err)
	} else if !state.IsZero() {
		dispatcher.ApplyRuntimePolicyState(state.Policy)
		for serviceName, item := range state.Services {
			reg.SetEnabled(serviceName, item.Enabled)
		}
	}

	if os.Getenv("EASY_PROTOCOL_ONCE") == "1" {
		log.Printf(
			"EasyProtocol framework ready on %s in %s mode with %d services",
			cfg.UnifiedAPI.Listen,
			cfg.Mode,
			len(cfg.Services),
		)
		return
	}

	srv := server.New(cfg, reg, refresher, coolingMgr, attributionMgr, statsMgr, traceStore, runtimeStore, controlPlaneStore, initialControlPlaneState, snapshotStore, auditStore, dispatcher)
	if err := srv.ListenAndServe(); err != nil {
		log.Fatalf("easy protocol server failed: %v", err)
	}
}
