# GitHub Actions Secrets

This repository now uses GitHub repository secrets as the hosted source of
truth for release-time credentials.

Add them in GitHub at:

`Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`

Fork users must add the same secret names to their own fork if they want to run
hosted publish there. Secret values do not transfer to forks.

## Supported Secret Mode

The main hosted gateway publish path is now:

- `.github/workflows/publish-service-base-ghcr.yml`

It uses granular `EASYPROTOCOL_*` secrets instead of committing a deployable
`config.yaml`.

## GHCR Publish Secrets

These are used to authenticate the image push:

| Secret name | Purpose | Format |
| --- | --- | --- |
| `EASYPROTOCOL_PUBLISH_GHCR_OWNER` | Optional GHCR owner override | Single line |
| `EASYPROTOCOL_PUBLISH_GHCR_USERNAME` | GHCR login username | Single line |
| `EASYPROTOCOL_PUBLISH_GHCR_TOKEN` | GHCR push token | Single line |
| `EASYPROTOCOL_PUBLISH_GHCR_REGISTRY` | Optional registry override. Default is `ghcr.io`. | Single line |

If username or token are omitted, the workflow falls back to `github.actor` and
`GITHUB_TOKEN` where possible.

## Hosted Config Materialization Secrets

These secrets are merged into a temporary root `config.yaml` during hosted
publish runs.

### Gateway Runtime

| Secret name | Purpose | Format |
| --- | --- | --- |
| `EASYPROTOCOL_SERVICE_UNIFIED_API_PASSWORD` | gateway unified API password | Single line |
| `EASYPROTOCOL_SERVICE_CONTROL_PLANE_READ_TOKEN` | control-plane read token | Single line |
| `EASYPROTOCOL_SERVICE_CONTROL_PLANE_MUTATE_TOKEN` | control-plane mutate token | Single line |

### Python Provider / Stack Runtime

| Secret name | Purpose | Format |
| --- | --- | --- |
| `EASYPROTOCOL_STACK_MAILBOX_SERVICE_API_KEY` | Python provider mailbox service API key | Single line |
| `EASYPROTOCOL_STACK_EASY_PROXY_API_KEY` | Python provider easy-proxy API key | Single line |
| `EASYPROTOCOL_PROVIDER_REGISTER_R2_ACCESS_KEY_ID` | Python provider register-output R2 upload access key id | Single line |
| `EASYPROTOCOL_PROVIDER_REGISTER_R2_SECRET_ACCESS_KEY` | Python provider register-output R2 upload secret key | Single line |
| `EASYPROTOCOL_PROVIDER_REGISTER_R2_ACCOUNT_ID` | Python provider register-output R2 account id | Single line |
| `EASYPROTOCOL_PROVIDER_REGISTER_R2_BUCKET` | Python provider register-output R2 bucket | Single line |
| `EASYPROTOCOL_PROVIDER_REGISTER_R2_ENDPOINT_URL` | Optional Python provider R2 endpoint override | Single line |
| `EASYPROTOCOL_PROVIDER_REGISTER_R2_REGION` | Optional Python provider R2 region override | Single line |
| `EASYPROTOCOL_PROVIDER_REGISTER_R2_PUBLIC_BASE_URL` | Optional Python provider R2 public base URL | Single line |

Optional host-path overrides for self-hosted workflows:

| Secret name | Purpose | Format |
| --- | --- | --- |
| `EASYPROTOCOL_PROVIDER_REGISTER_OUTPUT_DIR_HOST` | Host path for register output | Single line |
| `EASYPROTOCOL_PROVIDER_REGISTER_TEAM_AUTH_DIR_HOST` | Host path for team auth files | Single line |
| `EASYPROTOCOL_PROVIDER_REGISTER_TEAM_LOCAL_DIR_HOST` | Host path for local team auth store | Single line |

## Private R2 Runtime Config Distribution

`Publish Service Base GHCR` now also renders the final `service/base`
`config.yaml` and `runtime.env`, uploads them to a private R2 bucket, and emits
an encrypted owner-only import-code artifact.

Add these repository secrets:

| Secret name | Purpose | Format |
| --- | --- | --- |
| `EASYPROTOCOL_R2_CONFIG_ACCOUNT_ID` | Cloudflare account id that owns the R2 bucket | Single line |
| `EASYPROTOCOL_R2_CONFIG_BUCKET` | Private R2 bucket name for `service/base` runtime config | Single line |
| `EASYPROTOCOL_R2_CONFIG_ENDPOINT` | Optional explicit R2 S3 endpoint. Leave empty to derive from account id. | Single line |
| `EASYPROTOCOL_R2_CONFIG_CONFIG_OBJECT_KEY` | Object key for rendered `config.yaml` | Single line |
| `EASYPROTOCOL_R2_CONFIG_ENV_OBJECT_KEY` | Object key for rendered `runtime.env` | Single line |
| `EASYPROTOCOL_R2_CONFIG_MANIFEST_OBJECT_KEY` | Object key for the unified EasyProtocol distribution manifest | Single line |
| `EASYPROTOCOL_R2_CONFIG_UPLOAD_ACCESS_KEY_ID` | R2 upload access key id used by GitHub Actions | Single line |
| `EASYPROTOCOL_R2_CONFIG_UPLOAD_SECRET_ACCESS_KEY` | R2 upload secret access key used by GitHub Actions | Single line |

Optional repository-only admin storage for the client bootstrap key pair:

| Secret name | Purpose | Format |
| --- | --- | --- |
| `EASYPROTOCOL_R2_CONFIG_READ_ACCESS_KEY_ID` | Client-side R2 read-only access key id | Single line |
| `EASYPROTOCOL_R2_CONFIG_READ_SECRET_ACCESS_KEY` | Client-side R2 read-only secret access key | Single line |
| `EASYPROTOCOL_IMPORT_CODE_OWNER_PUBLIC_KEY` | Owner-only import-code encryption public key. GitHub Actions uses it to emit only an encrypted import-code artifact; keep the matching private key local. | Single line |

### Encrypted Import Code Output

After the R2 upload finishes, the workflow also generates an EasyProtocol
import code and immediately encrypts it with
`EASYPROTOCOL_IMPORT_CODE_OWNER_PUBLIC_KEY`.

The workflow publishes only the encrypted artifact:

- `service-base-import-code-encrypted`

To recover the plain import code locally, keep the matching private key on the
trusted operator machine and run:

```powershell
pwsh .\scripts\decrypt-import-code.ps1 `
  -EncryptedFilePath .\service-base-import-code.encrypted.json `
  -PrivateKeyPath C:\path\to\easyprotocol_import_code_owner_private.txt `
  -ImportCodeOnly
```

## Local Bootstrap Consumption

To turn an import code or a manifest back into a container-readable bootstrap
file, use:

```powershell
pwsh .\scripts\write-service-base-r2-bootstrap.ps1 `
  -ImportCode '<decoded-or-direct-import-code>' `
  -OutputPath .\deploy\service\base\config\bootstrap\r2-bootstrap.json
```

`deploy/service/base/docker-entrypoint.sh` now understands:

- `EASY_PROTOCOL_BOOTSTRAP_PATH`
- `EASY_PROTOCOL_IMPORT_CODE`
- `EASY_PROTOCOL_RUNTIME_ENV_PATH`

That means a new instance can start from:

1. a mounted rendered `config.yaml`
2. a mounted R2 bootstrap JSON
3. an `EASY_PROTOCOL_IMPORT_CODE` env var that is expanded into bootstrap at
   container startup

## Current Workflow Set

- `validate.yml`
  - runs repository validation
- `publish-service-base-ghcr.yml`
  - materializes config from GitHub secrets
  - runs smoke
  - publishes the gateway image to GHCR
  - uploads rendered runtime config to R2
  - emits an encrypted owner-only import code
- `publish-provider-images-ghcr.yml`
  - validates provider release tags
  - materializes config
  - publishes one or all provider images to GHCR
