# EasyProtocol Deploy Workspace

This directory holds the public monorepo Docker, publish, and smoke assets for
the EasyProtocol gateway runtime.

## Core Runtime Contract

- canonical runtime config: `/etc/easy-protocol/config.yaml`
- canonical state dir: `/var/lib/easy-protocol`
- minimum container environment:
  - `EASY_PROTOCOL_CONFIG_PATH`
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
  - prepares default config and state directories
- `docker-compose.yaml`
  - local docker compose entrypoint for the gateway runtime
- `scripts/publish-ghcr-easy-protocol-service.ps1`
  - local GHCR publish helper
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
