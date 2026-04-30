# EasyProtocol Request Response Contract

This file records the outward contract skeleton for `EasyProtocol`.

## Request Skeleton

An outward request is expected to carry, at minimum, logical fields like:

- request identity
- request mode
- target protocol operation
- payload body
- caller metadata
- preferred language service, if explicitly requested
- optional routing hints

## Response Skeleton

An outward response is expected to carry, at minimum, logical fields like:

- request identity
- handling service identity
- normalized status
- normalized result payload
- normalized error payload when failed
- normalized error attribution when failed
- timing or trace summary when available

## Routing-Aware Behavior

The outward contract should allow:

- no explicit language target, where `EasyProtocol` decides
- explicit language target, where `EasyProtocol` routes directly if allowed

## Important Rule

The outward request and response should stay unified even if the handling logic
behind the scenes is split across multiple language-specific services.
