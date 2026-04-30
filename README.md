# EasyProtocol

EasyProtocol is the public monorepo entrypoint for the EasyProtocol ecosystem.

It replaces the older multi-repository `ProtocolService` workspace with one
contributor-facing repository.

This repository intentionally avoids submodules. External contributors should
only need one repository checkout to inspect, build, test, and release the
main gateway and its provider runtimes.

## Planned Repository Layout

```text
service/
  base/
providers/
  python/
  go/
  javascript/
  rust/
deploy/
  service/
    base/
  providers/
    python/
    go/
    javascript/
    rust/
  stacks/
    easy-protocol/
docs/
scripts/
```

## Module Roles

### `service/base`

The unified outward-facing EasyProtocol gateway and control plane.

Responsibilities:

- public HTTP API
- control-plane API
- routing, fallback, cooling, stats, and trace storage
- provider registry and dispatch policy

### `providers/*`

Language-specific provider runtimes that perform protocol-side work.

Current provider families:

- `providers/python`
- `providers/go`
- `providers/javascript`
- `providers/rust`

The Python provider currently owns the hottest business path. The other
providers keep their language-local operations and can also act as adapters
around the Python runtime for selected `codex.*` flows.

The Python side is now intended to run as a single provider manager endpoint
that fans out real work into a dynamic subprocess pool with:

- minimum warm workers
- bounded maximum workers
- idle worker reap
- per-worker task recycling

## Configuration Direction

Like EasyEmail, this repository will converge on one root `config.yaml` as the
single human-edited operator config. Derived deploy files should be rendered
from that root config by scripts under `scripts/`.

Current bootstrap entrypoints:

- `scripts/init-config.ps1`
- `scripts/render-derived-configs.ps1`
- `scripts/test-all.ps1`
- `scripts/compile-service-base-image.ps1`
- `scripts/compile-provider-image.ps1`
- `scripts/deploy-service-base.ps1`
- `scripts/deploy-easyprotocol-stack.ps1`
- `scripts/deploy-easyprotocol-release.ps1`
- `scripts/deploy-isolated-easyprotocol-instance.ps1`
- `scripts/publish-provider-images.ps1`
- `scripts/deploy-subproject.ps1`
- `scripts/sync-from-protocolservice.ps1`
- `scripts/verify-structural-import.ps1`

See `docs/configuration.md` for the current root-config contract.

## Migration Rule

This repository is built by copy-only migration from the legacy
`ProtocolService` workspace:

- the source workspace stays untouched
- the new monorepo is reconstructed here
- source `.git` metadata and runtime artifacts are excluded

See `docs/migration-plan.md` for the source-to-target mapping.

## Documentation

- `docs/migration-plan.md`
- `docs/analysis/project-overview.md`
- `docs/analysis/module-inventory.md`
- `docs/analysis/risk-assessment.md`
- `docs/configuration.md`
- `docs/quickstart.md`
- `docs/github-actions-secrets.md`
- `docs/easyprotocol-release-workflow.md`
- `docs/python-protocol-manager-runtime-pool.md`
