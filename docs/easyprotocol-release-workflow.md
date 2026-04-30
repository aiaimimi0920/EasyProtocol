# EasyProtocol Release Workflow

The current hosted release automation centers on the gateway image publish
workflow:

- `.github/workflows/publish-service-base-ghcr.yml`
- `.github/workflows/publish-provider-images-ghcr.yml`

## Supported Triggers

- tag push:
  - `vX.Y.Z`
  - `release-YYYYMMDD-NNN`
  - `service-base-YYYYMMDD-NNN`
- manual `workflow_dispatch`

## What The Publish Workflow Does

1. materializes a temporary root `config.yaml`
2. renders the gateway config from the root config
3. optionally runs a local smoke image check
4. publishes the gateway image to GHCR
5. uploads the rendered `service/base` runtime config and `runtime.env` to
   private R2
6. generates an owner-only encrypted import-code artifact
7. emits a release manifest JSON artifact
8. emits release notes markdown
9. creates a GitHub Release on tag-triggered runs

## Provider Image Publish Workflow

The provider-image workflow currently supports:

- tag push:
  - `provider-python-YYYYMMDD-NNN`
  - `provider-go-YYYYMMDD-NNN`
  - `provider-javascript-YYYYMMDD-NNN`
  - `provider-rust-YYYYMMDD-NNN`
  - `providers-YYYYMMDD-NNN`
- manual dispatch with provider selection

It publishes:

- `python-protocol-service`
- `go-protocol-service`
- `javascript-protocol-service`
- `rust-protocol-service`

## Local Equivalent

For local release verification, use:

```powershell
.\scripts\deploy-easyprotocol-release.ps1 -ConfigPath .\config.yaml
```

That local flow currently performs:

- root config render
- gateway image build
- container smoke check
- optional `docker push`

## Current Scope

The first hosted release path currently covers the gateway image only.

The publish workflow now also produces the bootstrap material needed by a new
instance:

- `service-base-r2-config-manifest`
- `service-base-import-code-encrypted`

The plain import code is not published. Keep the matching private key local and
recover it with:

```powershell
pwsh .\scripts\decrypt-import-code.ps1 `
  -EncryptedFilePath .\service-base-import-code.encrypted.json `
  -PrivateKeyPath C:\path\to\easyprotocol_import_code_owner_private.txt `
  -ImportCodeOnly
```

The remaining missing layer is a remote deploy workflow. That should still
wait until the final public deployment target is explicitly chosen.
