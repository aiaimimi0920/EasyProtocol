# EasyProtocol EasyProxy Concept Mapping

This file records how the `EasyProxy` concepts are being mapped into
`EasyProtocol`.

## Mapping Summary

### `EasyProxy` mode

In `EasyProxy`, the top-level mode controls which runtime pattern is active.

In `EasyProtocol`, this maps to:

- `strategy` mode
- `specified` mode

### `EasyProxy` pool mode

In `EasyProxy`, `pool.mode` controls how the pool chooses a node:

- `sequential`
- `random`
- `balance`

In `EasyProtocol`, this maps to selector behavior inside strategy mode.

### `EasyProxy` failure threshold and blacklist duration

In `EasyProxy`, these values define node cooling and temporary exclusion.

In `EasyProtocol`, this maps to:

- service failure threshold
- service cooldown duration
- cooled-service rejection or avoidance

### `EasyProxy` monitor and store

In `EasyProxy`, monitor and store carry node state, errors, traffic, and
blacklist information.

In `EasyProtocol`, this maps to:

- unified service registry state
- service stats
- service cooldown state
- normalized error attribution records

### `EasyProxy` management API

In `EasyProxy`, the monitoring server exposes a unified management API.

In `EasyProtocol`, this maps to:

- unified outward API
- internal operator API
- registry, routing, cooling, and error inspection endpoints
