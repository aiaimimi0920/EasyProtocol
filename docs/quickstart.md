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
.\scripts\deploy-subproject.ps1 -Project easy-protocol -ConfigPath .\config.yaml
```

That canonical compose deploy ensures the external `EasyAiMi` Docker network
exists before startup and keeps:

- `easy-protocol`
- `easy-protocol-python-001`

inside the same `easy-protocol` compose project.

## 7. Run The One-Shot Local Service Release Flow

```powershell
.\scripts\deploy-easyprotocol-release.ps1 -ConfigPath .\config.yaml
```

## 8. Launch An Isolated New Instance

`easy-protocol` is now the canonical project name even for the isolated
gateway-plus-provider deployment. The underlying helper remains available, but
the preferred operator entrypoint is still the root wrapper:

```powershell
.\scripts\deploy-subproject.ps1 `
  -Project easy-protocol `
  -ConfigPath .\config.yaml `
  -InstanceName dyn01 `
  -GatewayHostPort 29789 `
  -PythonManagerHostPort 29103
```

This still joins `EasyAiMi`, but it keeps the gateway and provider children
under the same `easy-protocol` compose project for unified operator management.

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
