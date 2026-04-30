# Project Overview

## Transformation Summary

`EasyProtocol` is a public monorepo being reconstructed from the legacy
`ProtocolService` workspace.

The source workspace currently uses a workspace-root pattern:

- `repos/*` holds the real source repositories
- `deploy/*` holds container and stack assets
- the root itself is orchestration-oriented and not a finished application repo

The target monorepo replaces that shape with one contributor-facing repository.

## Source Workspace Observations

### Root role

The legacy root documents itself as an orchestration workspace rather than a
single application repository.

### Main runtime

`repos/EasyProtocol` is the outward-facing gateway:

- Go-based HTTP service
- registry refresh
- routing and dispatch
- fallback / retry policy
- cooldown and error attribution
- stats, traces, audit log, and control-plane state

### Provider runtimes

`repos/PythonProtocol`, `repos/GolangProtocol`, `repos/JSProtocol`, and
`repos/RustProtocol` are provider runtimes.

Current runtime reality:

- Python owns the hottest business path
- JS / Go / Rust retain language-local protocol operations
- JS / Go / Rust can forward selected `codex.*` operations to Python

Target runtime direction inside the public monorepo:

- one Python provider manager endpoint registered in the gateway
- that manager maintains a dynamic subprocess pool instead of a fixed set of
  always-on execution containers

### Deploy assets

The legacy workspace already contains:

- a gateway Dockerfile and compose under `deploy/EasyProtocol`
- provider Dockerfiles under `deploy/PythonProtocol`, `deploy/GolangProtocol`,
  `deploy/JSProtocol`, and `deploy/RustProtocol`
- an integration compose stack under `deploy/EasyStack`

### CI / release posture

The legacy `.github/workflows` directory is still a placeholder.

That means the public monorepo must add new root-level CI / release automation
instead of trying to preserve an existing hosted workflow contract.

## Target Monorepo Direction

The public repository will converge on:

- `service/base` for the gateway runtime
- `providers/*` for the language-specific runtimes
- `deploy/service/base`, `deploy/providers/*`, and `deploy/stacks/*`
- `scripts/` for root entrypoints
- one root `config.yaml` as the operator source of truth

## Main Entry Points To Preserve

### Gateway

- `service/base/cmd/easy_protocol/main.go`
- gateway API docs and config model

### Python provider

- `providers/python/src/server.py`
- `providers/python/src/new_protocol_register/easyprotocol_flow.py`
- provider-local shared modules currently living under `python_shared/`

### Other providers

- `providers/javascript/src/server.js`
- `providers/go/cmd/golang_protocol/main.go`
- `providers/rust/src/main.rs`

## Toolchain Snapshot

- gateway: Go
- python provider: Python 3.10-oriented runtime plus Go helper install
- javascript provider: Node.js
- rust provider: Rust
- deploy assets: Docker / Docker Compose / PowerShell

This mixed toolchain is acceptable in the public monorepo as long as the root
docs and automation make module boundaries clear.
