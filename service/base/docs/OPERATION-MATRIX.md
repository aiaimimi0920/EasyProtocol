# EasyProtocol Default Operation Matrix

This file records the current default operation split across language services.

## Shared Operations

These are intentionally available on multiple services so `EasyProtocol` can
demonstrate strategy routing, fallback, and preferred-service behavior.

- `health.inspect`
- `protocol.echo`
- `protocol.query.encode` (`JSProtocol`, `GolangProtocol`)
- `protocol.regex.extract` (`PythonProtocol`, `JSProtocol`)
- `protocol.hash.sha256` (`RustProtocol`, `GolangProtocol`)

## Service-Specific Operations

### `GolangProtocol`

- `protocol.headers.normalize`

### `JSProtocol`

- `protocol.template.render`
- `protocol.json.compact`

### `PythonProtocol`

- `protocol.text.slugify`
- `protocol.data.flatten`

### `RustProtocol`

- `protocol.bytes.hex`
- `protocol.bytes.xor`

## Default Preference Order

When `EasyProtocol` runs in `strategy` mode, it first filters by capability,
health, enabled state, and cooldown. After that it uses the default
per-operation preferred service order:

- `protocol.query.encode` -> `JSProtocol`, then `GolangProtocol`
- `protocol.regex.extract` -> `PythonProtocol`, then `JSProtocol`
- `protocol.hash.sha256` -> `RustProtocol`, then `GolangProtocol`

Callers can still override the strategy path with:

- `mode=specified`
- `routing_hints.preferred_language`
