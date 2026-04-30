# EasyProtocol Architecture

`EasyProtocol` is the unified outward-facing service inside the
`ProtocolService` workspace.

It is the stable entry layer that external callers should talk to first.

## Core Role

`EasyProtocol` is responsible for:

- exposing a unified external interface
- resolving which language-specific protocol service should handle a request
- normalizing the service-facing request and response boundary
- managing registry, routing, and delegation decisions
- applying strategy mode or specified mode
- applying service cooling decisions
- recording normalized error attribution

`EasyProtocol` is not the place where language-specific protocol execution
details should live.

Those details belong in:

- `GolangProtocol`
- `JSProtocol`
- `PythonProtocol`
- `RustProtocol`

## Layering Model

High-level flow:

1. caller submits request to `EasyProtocol`
2. `EasyProtocol` validates the outward envelope
3. `EasyProtocol` resolves a target language service
4. request is delegated to the selected language-specific service
5. response is normalized back to the outward-facing shape

## Main Internal Areas

### `api/`

Owns the outward-facing service entrypoints.

### `config/`

Owns configuration shape and service-level switches.

### `registry/`

Owns the logical registry of known language-specific services.

### `routing/`

Owns target-selection and dispatch-decision logic.

### `strategy/`

Owns strategy-mode selection rules and selector contracts.

### `cooling/`

Owns service cooling rules and cooldown state shapes.

### `attribution/`

Owns normalized error attribution categories and attribution records.

### `stats/`

Owns service-facing aggregated statistics shapes.

### `services/`

Owns orchestration-level service logic such as request dispatch.

### `transports/`

Owns outward transport boundary definitions.

## Important Boundary

`EasyProtocol` should define the unified contract surface, but it should avoid
embedding language-runtime-specific implementation details directly.
