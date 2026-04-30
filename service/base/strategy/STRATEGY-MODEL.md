# EasyProtocol Strategy Model

This file records the strategy-mode framework skeleton.

## Strategy Responsibilities

Strategy mode should:

- inspect all enabled language services
- exclude cooled services
- exclude unsupported services
- choose a candidate according to the configured selector style

## Selector Styles

Current planned selector styles:

- `sequential`
- `random`
- `balance`

## Selector Inputs

Selectors may eventually consume:

- service availability
- service cooling state
- recent error counts
- active load
- capability compatibility
