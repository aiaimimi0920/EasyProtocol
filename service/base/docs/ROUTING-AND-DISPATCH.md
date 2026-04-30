# EasyProtocol Routing And Dispatch

This file records the current route and dispatch skeleton.

## High-Level Flow

1. receive outward request
2. validate request shape
3. inspect explicit language target, if any
4. query language-service registry
5. resolve routing decision
6. delegate to chosen language service
7. normalize returned result

## Current Routing Modes

### Direct Language Mode

Use the caller-selected language service when:

- an explicit target is present
- the service is enabled
- the service claims support for the requested operation

### Resolved Language Mode

Let `EasyProtocol` choose when:

- no explicit language target is given
- the outward interface is expected to remain language-agnostic

## Framework Modes

### Strategy Mode

In strategy mode, `EasyProtocol` should:

- inspect the registry
- filter unavailable or cooled services
- apply selector rules
- choose the best candidate service

### Specified Mode

In specified mode, `EasyProtocol` should:

- respect the caller-selected service target
- reject the request if that service is disabled, cooled, or unsupported

## Dispatch Outcome Categories

Current normalized outcomes should eventually include:

- delegated successfully
- rejected by validation
- rejected because no service supports the request
- rejected because the selected service is disabled
- rejected because the selected service is cooled
- failed during delegation
