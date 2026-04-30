# EasyProtocol Unified API Interface

This file records the current unified API skeleton for `EasyProtocol`.

## API Goal

`EasyProtocol` should be the single outward entrypoint that callers integrate
with first.

Callers should not be required to directly bind to:

- `GolangProtocol`
- `JSProtocol`
- `PythonProtocol`
- `RustProtocol`

unless an explicit internal or advanced use case requires it later.

## Main API Concerns

The unified API should eventually support:

- request submission
- strategy-mode execution
- specified-mode execution
- service capability inspection
- health and cooling inspection
- error attribution inspection
- stats inspection

## Internal Operator Layer

An internal or operator-facing API surface should eventually expose:

- registry state
- route decisions
- cooldown state
- error attribution records
- service stats
