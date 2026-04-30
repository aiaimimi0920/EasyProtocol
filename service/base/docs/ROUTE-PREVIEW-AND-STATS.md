# EasyProtocol Route Preview And Stats

This file records the current diagnostics layer added on top of the unified
dispatch flow.

## Route Preview

`EasyProtocol` now exposes route preview endpoints so callers or operators can
see how a request would be routed before execution.

The preview currently includes:

- normalized operation
- requested service
- preferred language hint
- configured preferred services for that operation
- selected service
- fallback chain
- per-service candidate diagnostics

Candidate diagnostics include:

- enabled state
- health-known / healthy state
- cooled state
- support for the requested operation
- active request count
- eligibility
- exclusion reason

## Fallback Chain

The fallback chain is diagnostic metadata that shows the current preferred
service order after capability, health, and cooldown filtering.

`EasyProtocol` now also uses the chain for runtime fallback in strategy mode
when a downstream failure is classified as retryable.

Current retryable categories are centered on routing-adjacent or transport-like
failures, such as:

- transport/delegation/runtime/timeout failures
- temporary unavailability
- stale capability mismatches such as downstream `unsupported_operation`

Specified mode still stays pinned to the caller-selected service and does not
fall through to a different language service.

## Route Traces

`EasyProtocol` now stores recent route traces and exposes them through the
internal API.

Each trace records:

- request id
- operation
- initial preview
- fallback chain
- per-attempt outcome
- whether a retry occurred
- final selected service
- final status / error category

The trace API now also has a summary endpoint so operators can aggregate recent
routing behavior by operation and by final service.

## Operation-Level Fallback Policy

`EasyProtocol` now supports both global and operation-specific retry policy.

Global strategy policy covers:

- whether fallback-on-retryable-errors is enabled
- max fallback attempts
- retryable categories

Per-operation policy can override:

- fallback mode (`enabled` or `disabled`)
- max fallback attempts
- retryable categories

This makes it possible to keep broad strategy fallback enabled while carving
out stricter behavior for operations that should stay single-attempt.

## Route Simulator

`EasyProtocol` now also exposes a route simulator endpoint for operator use.

The simulator compares:

- baseline effective policy
- baseline preview
- simulated effective policy
- simulated preview
- a compact diff map

This is useful when evaluating how a change to preferred services or fallback
policy would alter routing behavior before changing the real configuration.

## Runtime Persistence And Audit

`EasyProtocol` now supports persistence for the current runtime control-plane
state.

The persisted state currently covers:

- global retry/fallback policy
- per-operation preferred services
- per-operation policy
- service enabled/disabled state

Operator mutations are also appended to an audit log so the control plane can
answer:

- what changed?
- which target did it affect?
- when did it happen?
- which actor or reason was attached?

Runtime-state mutations now also persist explicit rollback snapshots, so the
operator layer can:

- export the current runtime state
- import a known-good state snapshot
- inspect recent persisted snapshots
- roll back to a previous snapshot id
- inspect structured before/after diffs in audit history

The control plane is now also guarded by token-based internal authentication
and actor requirements for mutating routes, so runtime governance is no longer
anonymous by default.

The security layer now also supports:

- read-token vs mutate-token separation
- token rotation without restart
- previous-token grace windows during staged rotation
- read-only freeze mode for mutating internal actions
- maintenance mode for public execution requests
- maintenance reason / ETA persistence
- maintenance started-at persistence
- last-maintenance completion summaries after maintenance windows close
- public status exposure for maintenance windows
- in-flight drain summaries for operator maintenance workflows
- localhost-only / allowlist restrictions for internal traffic
- denied-access audit records
- security-enriched mutation audit details
- aggregated control-plane security-event summaries
- hourly and daily security trend buckets
- top actors and top operations in security summaries
- audit-log retention inspection and pruning

## Operation-Level Stats

`EasyProtocol` now records:

- service-level stats
- operation-level stats
- service + operation stats

This makes it possible to answer questions like:

- which operation is failing most often?
- which service currently carries most `protocol.regex.extract` load?
- which service-operation pairs hit cooldown most often?
