# EasyProtocol Execution Modes

This file records the framework modes supported by `EasyProtocol`.

## Mode 1: Strategy

Strategy mode means:

- the caller uses the unified API
- the caller may omit the target language service
- `EasyProtocol` decides which language-specific service should handle the
  request

Strategy mode should be able to use selector styles such as:

- `sequential`
- `random`
- `balance`

## Mode 2: Specified

Specified mode means:

- the caller explicitly chooses a language-specific service
- `EasyProtocol` routes directly to that target
- no fallback to other services should occur unless such behavior is
  intentionally added later

## Important Rule

The outward API remains unified in both modes.

The difference is only how the handling service is selected.
