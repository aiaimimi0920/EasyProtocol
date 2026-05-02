# EasyProtocol

EasyProtocol is the public monorepo entrypoint for the EasyProtocol ecosystem.

It replaces the older multi-repository `ProtocolService` workspace with one
contributor-facing repository.

This repository intentionally avoids submodules. External contributors should
only need one repository checkout to inspect, build, test, and release the
main gateway and its provider runtimes.

## Development Workflow

See `docs/development-workflow.md` for the shared cross-repository development
rules used for local-first iteration, temporary test assets, and final
GHCR-based validation.

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

- `deploy-host.ps1`
- `scripts/init-config.ps1`
- `scripts/render-derived-configs.ps1`
- `scripts/test-all.ps1`
- `scripts/compile-service-base-image.ps1`
- `scripts/compile-provider-image.ps1`
- `scripts/deploy-service-base.ps1`
- `scripts/deploy-easyprotocol-stack.ps1`
- `scripts/deploy-easyprotocol-release.ps1`
- `scripts/deploy-isolated-easyprotocol-instance.ps1`
- `scripts/upload-service-base-r2-config.ps1`
- `scripts/write-service-base-r2-bootstrap.ps1`
- `scripts/decrypt-import-code.ps1`
- `scripts/publish-provider-images.ps1`
- `scripts/deploy-subproject.ps1`
- `scripts/sync-from-protocolservice.ps1`
- `scripts/verify-structural-import.ps1`

See `docs/configuration.md` for the current root-config contract.

## Hosted Publish Direction

The service-base publish workflow now follows the same operator pattern used by
EasyEmail:

- GitHub repository secrets are materialized into a temporary root `config.yaml`
- the gateway image is built and pushed to GHCR
- the rendered runtime config is uploaded to private Cloudflare R2
- an owner-only encrypted import-code artifact is generated for bootstrap

See `docs/github-actions-secrets.md` and
`docs/easyprotocol-release-workflow.md` for the exact secret names and the
import-code flow.

## Local EasyProtocol Docker Deploy

The canonical local Docker rollout for the public repository is:

- GitHub Actions publish the gateway image to GHCR
- the target host checks out this repository
- a root PowerShell script pulls the tagged GHCR image and deploys the gateway
  container into Docker

This GHCR deploy path covers the `service/base` gateway container. It expects
the target host to have access to any provider runtimes or upstream services
the rendered gateway config points at.

Prerequisites:

- Windows PowerShell
- Docker Desktop or another Docker engine with `docker compose`
- Python 3 with `PyYAML`
- a local checkout of this repository

Prepare the root config:

```powershell
.\scripts\init-config.ps1
```

Then edit `config.yaml`. At minimum, fill in:

- `publishing.ghcr.owner`
  - the GitHub owner or org that publishes
    `ghcr.io/<owner>/easy-protocol-service:<release-tag>`
- `serviceBase.runtime.unified_api.password`
- `serviceBase.runtime.control_plane.read_token`
- `serviceBase.runtime.control_plane.mutate_token`
- any provider or dependency values your gateway needs at runtime

Recommended local GHCR rollout command:

```powershell
.\deploy-host.ps1 `
  -ReleaseTag release-20260502-001
```

That root entrypoint is intentionally single-file distributable. If an operator
downloads only `deploy-host.ps1`, it can bootstrap a local repo cache
automatically before invoking the canonical deployment path.

Lower-level GHCR rollout command:

```powershell
.\scripts\deploy-service-base.ps1 `
  -ConfigPath .\config.yaml `
  -FromGhcr `
  -ReleaseTag release-20260502-001
```

Equivalent root one-click wrapper:

```powershell
.\scripts\deploy-subproject.ps1 `
  -Project service-base-ghcr `
  -ConfigPath .\config.yaml `
  -ReleaseTag release-20260502-001
```

You can also pin the full image reference directly:

```powershell
.\scripts\deploy-service-base.ps1 `
  -ConfigPath .\config.yaml `
  -FromGhcr `
  -Image ghcr.io/<owner>/easy-protocol-service:<release-tag>
```

What the root deploy script does:

- renders `deploy/service/base/config/config.yaml`
- renders `deploy/service/base/config/runtime.env`
- ensures the external Docker network exists
- pulls the requested GHCR image unless `-SkipPull` was passed
- writes the runtime `.env` used by `deploy/service/base/docker-compose.yaml`
- replaces the existing `easyprotocol-service-base` container if one already exists
- runs Docker Compose to bring the gateway back up

Recommended post-deploy checks:

```powershell
docker ps --filter "name=easyprotocol-service-base"

Invoke-RestMethod -Uri "http://127.0.0.1:19788/health" -Method Get
```

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
- `docs/root-host-deploy-standard.md`
