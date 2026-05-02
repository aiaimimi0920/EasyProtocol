# EasyProtocol Deploy Workspace

This directory holds the public monorepo Docker, publish, and smoke assets for
the EasyProtocol gateway runtime.

## Core Runtime Contract

- canonical runtime config: `/etc/easy-protocol/config.yaml`
- canonical state dir: `/var/lib/easy-protocol`
- minimum container environment:
  - `EASY_PROTOCOL_CONFIG_PATH`
  - `EASY_PROTOCOL_RUNTIME_ENV_PATH`
  - `EASY_PROTOCOL_BOOTSTRAP_PATH`
  - `EASY_PROTOCOL_STATE_DIR`
  - `EASY_PROTOCOL_RESET_STORE_ON_BOOT`

Runtime policy, control-plane tokens, and service registry definitions should
flow from the root `config.yaml` through the render step instead of being
edited directly in tracked deploy-local files.

## Key Files

- `Dockerfile`
  - builds `easy-protocol-service` from `service/base`
- `config.template.yaml`
  - template used by the root render flow
- `docker-entrypoint.sh`
  - prepares default config and state directories and can bootstrap from R2
- `bootstrap-service-config.py`
  - fetches rendered runtime config artifacts from private R2
- `docker-compose.yaml`
  - local docker compose entrypoint for the gateway runtime
- `scripts/publish-ghcr-easy-protocol-service.ps1`
  - local GHCR publish helper
- `scripts/deploy-ghcr-easy-protocol-service.ps1`
  - local GHCR deploy helper for the gateway runtime
- `scripts/smoke-easy-protocol-docker-api.ps1`
  - local container API smoke helper

## Root Config Direction

Render the deploy-local config from the repository root before local compose
runs:

```powershell
.\scripts\render-derived-configs.ps1 -ServiceBase
```

The generated output currently lands in:

- `deploy/service/base/config/config.yaml`
- `deploy/service/base/config/runtime.env`

## Import-Code Bootstrap

The service-base image can now start from a plain rendered config or from a
private R2 bootstrap flow.

Supported startup inputs:

- mounted `config.yaml`
- mounted bootstrap file at `/etc/easy-protocol/bootstrap/r2-bootstrap.json`
- `EASY_PROTOCOL_IMPORT_CODE`, which is converted into a bootstrap file at
  container startup

The import-code helpers live at repo root:

- `scripts/easyprotocol-import-code.py`
- `scripts/write-service-base-r2-bootstrap.ps1`
- `scripts/decrypt-import-code.ps1`

## Current Integration Boundary

The gateway still fronts these provider families:

- `PythonProtocol`
- `GolangProtocol`
- `JSProtocol`
- `RustProtocol`

Current main-flow operations:

- `codex.register.protocol`
- `codex.repair.protocol`
- `codex.semantic.step`

Default strategy order still prefers:

1. `PythonProtocol`
2. `GolangProtocol`
3. `JSProtocol`
4. `RustProtocol`

## Quick Start

```powershell
cd C:\Users\Public\nas_home\AI\GameEditor\EasyProtocol
.\scripts\render-derived-configs.ps1 -ServiceBase
docker compose -f .\deploy\service\base\docker-compose.yaml up -d --build
```

Default port: `http://127.0.0.1:19788`

## Local GHCR Deploy

Canonical root entrypoint:

```powershell
.\scripts\deploy-service-base.ps1 `
  -ConfigPath .\config.yaml `
  -FromGhcr `
  -ReleaseTag <release-tag>
```

One-click wrapper:

```powershell
.\scripts\deploy-subproject.ps1 `
  -Project service-base-ghcr `
  -ConfigPath .\config.yaml `
  -ReleaseTag <release-tag>
```

Lower-level helper:

```powershell
.\deploy\service\base\scripts\deploy-ghcr-easy-protocol-service.ps1 `
  -ConfigPath .\deploy\service\base\config\config.yaml `
  -RuntimeEnvPath .\deploy\service\base\config\runtime.env `
  -Image ghcr.io/<owner>/easy-protocol-service:<release-tag>
```
