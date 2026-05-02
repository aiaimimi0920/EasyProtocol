# Quick Start

## 1. Initialize The Root Config

```powershell
.\scripts\init-config.ps1
```

That creates `config.yaml` from `config.example.yaml`.

Edit only the root `config.yaml`.

## 2. Render Derived Config Files

```powershell
.\scripts\render-derived-configs.ps1
```

Current generated outputs:

- `deploy/service/base/config/config.yaml`
- `deploy/stacks/easy-protocol/generated/easy-protocol.config.yaml`
- `deploy/stacks/easy-protocol/generated/stack.env`

## 3. Validate The Repository

```powershell
.\scripts\test-all.ps1 -ConfigPath .\config.yaml
.\scripts\verify-structural-import.ps1
```

## 4. Build The Gateway Image

```powershell
.\scripts\compile-service-base-image.ps1 -ConfigPath .\config.yaml
```

## 5. Run The Gateway Locally

```powershell
.\scripts\deploy-service-base.ps1 -ConfigPath .\config.yaml
```

To pull a published GHCR gateway image instead of building locally:

```powershell
.\scripts\deploy-service-base.ps1 `
  -ConfigPath .\config.yaml `
  -FromGhcr `
  -ReleaseTag release-20260502-001
```

Equivalent root wrapper:

```powershell
.\scripts\deploy-subproject.ps1 `
  -Project service-base-ghcr `
  -ConfigPath .\config.yaml `
  -ReleaseTag release-20260502-001
```

## 6. Run The Full EasyProtocol Stack

```powershell
.\scripts\deploy-easyprotocol-stack.ps1 -ConfigPath .\config.yaml
```

That stack and the standalone gateway deploy both ensure the external
`EasyAiMi` Docker network exists before startup.

## 7. Run The One-Shot Local Service Release Flow

```powershell
.\scripts\deploy-easyprotocol-release.ps1 -ConfigPath .\config.yaml
```

## 8. Launch An Isolated New Instance

This is the safe way to bring up a new gateway + python manager instance
without touching older running containers:

```powershell
.\scripts\deploy-isolated-easyprotocol-instance.ps1 `
  -ConfigPath .\config.yaml `
  -InstanceName dyn01 `
  -GatewayHostPort 29789 `
  -PythonManagerHostPort 29103
```

The isolated instance still joins `EasyAiMi`, but it uses its own container
names, host ports, config mount, and data directory.

## 9. Build Provider Images

Build one provider image:

```powershell
.\scripts\compile-provider-image.ps1 -Provider python -ConfigPath .\config.yaml
```

Build all provider images through the unified root entrypoint:

```powershell
.\scripts\deploy-subproject.ps1 -Project build-provider-images -ProviderTarget all
```

## 10. Re-Sync From The Legacy Source Workspace

When the legacy `ProtocolService` workspace changes and you want to replay the
copy-only migration into this public monorepo:

```powershell
.\scripts\sync-from-protocolservice.ps1
```

That script only mutates the new `EasyProtocol` repository. It never writes
back into the legacy source workspace.
