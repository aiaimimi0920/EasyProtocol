# EasyProtocol API Surface

This file describes the currently implemented HTTP surface for `EasyProtocol`.

## Public Surface

### `GET /api/health`

Returns unified service health for the `EasyProtocol` entrypoint.

### `GET /api/public/status`

Returns public-safe runtime availability status for callers.

Current fields include:

- whether the unified entrypoint is currently available for execution
- maintenance mode state
- maintenance reason / ETA
- maintenance started-at / elapsed / ETA-remaining metadata
- last completed maintenance summary when one is available

### `GET /api/public/capabilities`

Returns:

- currently registered downstream services
- current selector mode
- global retry / fallback policy
- supported operation matrix
- configured per-operation preferred service order
- configured per-operation fallback policy

### `POST /api/public/request`

Unified execution entrypoint.

Current request fields:

- `request_id`
- `mode` (`strategy` or `specified`)
- `operation`
- `payload`
- `requested_service`
- `routing_hints.preferred_language`

### `POST /api/public/route-preview`

Returns a non-mutating route preview for a request envelope.

Current preview output includes:

- normalized operation
- selected service
- route reason
- fallback chain
- fallback enabled
- max fallback attempts
- retryable categories
- per-candidate eligibility diagnostics

## Internal Surface

When `control_plane.enabled=true`, internal endpoints require control-plane
authentication.

Supported credential transport:

- `Authorization: Bearer <token>`
- `X-EasyProtocol-Token: <token>`

Scope model:

- read-only internal endpoints accept the read token or mutate token
- mutating internal endpoints require the mutate token
- when freeze mode is enabled, non-security mutating endpoints return a locked response
- when `localhost_only=true`, internal requests must originate from loopback
- when `allowlist` is configured, internal requests must originate from an allowed IP/CIDR

Mutating internal endpoints also require an operator identity when
`control_plane.require_actor=true`.

Supported actor/reason sources:

- JSON request body fields such as `actor` / `reason`
- `X-EasyProtocol-Actor`
- `X-EasyProtocol-Reason`

### `GET /api/internal/control-plane`

Returns the current control-plane security summary.

Current summary includes:

- whether control-plane protection is enabled
- whether actor enforcement is enabled
- whether maintenance mode is enabled
- maintenance reason and ETA when set
- maintenance started-at / elapsed / ETA-remaining metadata
- whether freeze mode is enabled
- whether localhost-only or allowlist network restrictions are active
- whether read/mutate tokens are configured
- current and previous token fingerprints / grace-until metadata
- short token fingerprints for operator verification
- last completed maintenance summary

### `GET /api/internal/control-plane/drain`

Returns the current in-flight drain summary for operator maintenance workflows.

Current summary includes:

- whether maintenance mode is enabled
- total active downstream requests
- active services
- active operations
- drain-friendly maintenance context such as elapsed / remaining window
- last completed maintenance summary for quick operator reference

### `GET /api/internal/control-plane/security-events`

Returns an aggregated summary of recent security-oriented audit events.

Current summary includes:

- denied-access counts
- denied-access reasons
- token rotation count
- freeze / maintenance change counts
- public request rejection count
- top affected targets
- top client IPs
- top paths
- top actors
- top operations
- hourly and daily trend buckets
- grace-cleanup counts
- recent security events

### `POST /api/internal/control-plane/maintenance`

Enables or disables maintenance mode for public request execution.

When enabled:

- `POST /api/public/request` returns a service-unavailable failure
- public route preview remains available
- maintenance state is persisted and survives restart
- maintenance reason / ETA are exposed through control-plane summary and failure details
- disabling maintenance captures a completion summary including duration and rejected-request totals

### `POST /api/internal/control-plane/freeze`

Enables or disables read-only freeze mode for the internal control plane.

Payload fields:

- `enabled`
- `actor`
- `reason`

### `POST /api/internal/control-plane/tokens/rotate`

Rotates the read token and/or mutate token without restarting the service.

Payload fields:

- `read_token`
- `mutate_token`
- `grace_period_seconds`
- `actor`
- `reason`

### `GET /api/internal/registry`

Returns current downstream registry state.

### `POST /api/internal/registry/refresh`

Refreshes downstream `/health` and `/capabilities`.

### `GET /api/internal/runtime-state`

Returns the current runtime state snapshot that is used for persistence.

### `GET /api/internal/runtime-state/export`

Alias of the runtime-state endpoint for operator export tooling.

### `POST /api/internal/runtime-state/import`

Imports a full runtime-state snapshot and applies it immediately.

The import payload accepts:

- `actor`
- `reason`
- `state`

Imported state currently covers:

- global retry/fallback policy
- per-operation preferred services
- per-operation policy overrides
- per-service enabled state

### `GET /api/internal/runtime-state/snapshots`

Returns recent persisted runtime-state snapshot entries.

Optional query:

- `limit`

### `POST /api/internal/runtime-state/rollback`

Rolls the runtime control plane back to a prior snapshot id.

Payload fields:

- `snapshot_id`
- `actor`
- `reason`

### `GET /api/internal/audit-log`

Returns recent operator mutation history.

Optional queries:

- `action`
- `target_type`
- `target`
- `limit`

Audit records now include a `details.diff` block for runtime-state mutations and,
when snapshot persistence is enabled, the `snapshot_id` that captured the
post-mutation state.

### `GET /api/internal/audit-log/retention`

Returns current audit-log retention state.

Current fields include:

- configured retention limit
- current retained record count
- file existence / file size
- oldest and newest retained record timestamps

### `POST /api/internal/audit-log/prune`

Prunes retained audit history while preserving an auditable prune event.

Payload fields:

- `keep`
- `actor`
- `reason`

### `POST /api/internal/route-preview`

Internal mirror of the public route preview endpoint for operator tooling.

### `GET /api/internal/policies`

Returns current global fallback policy and per-operation effective policy.

### `POST /api/internal/policies/update`

Applies runtime policy changes without restarting the service.

Supports:

- global retry/fallback changes
- per-operation preferred service changes
- per-operation fallback policy changes
- resetting operation policy or preferred-service overrides

### `POST /api/internal/route-simulator`

Simulates route selection with optional policy overrides.

Supported override fields:

- `preferred_services`
- `fallback_mode`
- `max_fallback_attempts`
- `retryable_categories`

### `POST /api/internal/services/action`

Runs service-level operator actions.

Current actions:

- `enable`
- `disable`
- `refresh`
- `reset_cooling`
- `reset_stats`
- `reset_health`

### `GET /api/internal/cooling`

Returns cooldown state by service.

### `POST /api/internal/cooling/reset`

Resets cooldown state globally or for one service.

### `GET /api/internal/stats`

Returns runtime stats grouped as:

- `services`
- `operations`
- `service_operations`

### `POST /api/internal/stats/reset`

Resets stats globally or for one service.

### `GET /api/internal/errors`

Returns recent normalized attribution records.

### `POST /api/internal/errors/clear`

Clears recent attribution history.

### `GET /api/internal/route-traces`

Returns recent route traces.

Optional query:

- `request_id`

### `POST /api/internal/route-traces/clear`

Clears stored route traces.

### `GET /api/internal/route-traces/summary`

Returns aggregated trace diagnostics.

Optional queries:

- `operation`
- `service`
- `status`

### `GET /api/internal/diagnostics/overview`

Returns a bundled operator overview including:

- registry
- runtime state
- cooling
- stats
- trace summary
- top failing operations
- recent errors
- recent audit log
- control-plane auth summary
- security-event summary
- policies

## Response Model

Successful responses return:

- `selected_service`
- `status=succeeded`
- normalized `result`
- `meta.request_mode`
- `meta.strategy_mode`
- `meta.route_reason`
- `meta.fallback_chain`
- `meta.trace_id`
- `meta.attempt_count`
- `meta.retried`

Failed responses return:

- `status=failed`
- normalized attribution `error`
- retry / trace metadata in `meta.trace_id`, `meta.attempt_count`, `meta.retried`
- cooldown information in `meta.cooldown_applied`
