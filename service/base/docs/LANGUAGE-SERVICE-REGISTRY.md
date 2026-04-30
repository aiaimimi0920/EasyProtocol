# EasyProtocol Language Service Registry

This file records the current registry skeleton for language-specific services.

## Registry Purpose

`EasyProtocol` needs a registry so it can reason about:

- which language services exist
- whether they are enabled
- what operations they claim to support
- how requests should be delegated to them

## Current Registered Service Identities

- `GolangProtocol`
- `JSProtocol`
- `PythonProtocol`
- `RustProtocol`

## Logical Registry Fields

Each service record will likely need fields such as:

- service name
- language kind
- enabled state
- supported operations
- health-known / healthy state
- last refresh status
- supported protocol versions
- transport endpoint metadata
- cooling eligibility

## Current Default Operation Split

- `GolangProtocol`: query encoding, header normalization, SHA-256 fallback
- `JSProtocol`: template rendering, JSON compaction, query encoding fallback
- `PythonProtocol`: regex extraction, slugify, nested data flattening
- `RustProtocol`: SHA-256 primary path, byte hex/xor transforms

## Registry Boundary

The registry belongs to the outward service layer because `EasyProtocol` must
be the place that understands all downstream language services together.
