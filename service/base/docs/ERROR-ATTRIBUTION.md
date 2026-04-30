# EasyProtocol Error Attribution

This file records the current error attribution skeleton for `EasyProtocol`.

## Goal

`EasyProtocol` should normalize failures into stable attribution categories so
the outward layer and operator layer can reason about errors consistently.

## Example Attribution Categories

- `validation_error`
- `unsupported_operation`
- `no_service_available`
- `service_not_found`
- `service_disabled`
- `service_cooled`
- `routing_error`
- `delegation_error`
- `transport_error`
- `service_runtime_error`
- `timeout_error`

## Attribution Record Purpose

Attribution should help answer:

- why a request failed
- whether the failure should count toward cooling
- whether the failure belongs to the outward layer or the language service
- which service was selected or attempted
