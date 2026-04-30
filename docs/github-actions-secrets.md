# GitHub Actions Secrets

This repository currently uses GitHub repository secrets for GHCR publishing
and optional config materialization.

Add them in GitHub at:

`Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`

## GHCR Publish Secrets

These are used by `.github/workflows/publish-service-base-ghcr.yml`.

| Secret name | Purpose |
| --- | --- |
| `EASYPROTOCOL_PUBLISH_GHCR_OWNER` | Explicit GHCR owner override |
| `EASYPROTOCOL_PUBLISH_GHCR_USERNAME` | GHCR login username |
| `EASYPROTOCOL_PUBLISH_GHCR_TOKEN` | GHCR push token |
| `EASYPROTOCOL_PUBLISH_GHCR_REGISTRY` | Optional registry override, defaults to `ghcr.io` |

If the username or token are omitted, the workflow falls back to the GitHub
actor and `GITHUB_TOKEN` where possible.

## Service Runtime Secrets

These can be materialized into a temporary root `config.yaml` during hosted
publish runs.

| Secret name | Purpose |
| --- | --- |
| `EASYPROTOCOL_SERVICE_UNIFIED_API_PASSWORD` | gateway unified API password |
| `EASYPROTOCOL_SERVICE_CONTROL_PLANE_READ_TOKEN` | control-plane read token |
| `EASYPROTOCOL_SERVICE_CONTROL_PLANE_MUTATE_TOKEN` | control-plane mutate token |

## Stack / Provider Secrets

These can also be materialized into the temporary root config:

| Secret name | Purpose |
| --- | --- |
| `EASYPROTOCOL_STACK_MAILBOX_SERVICE_API_KEY` | Python provider mailbox service API key |
| `EASYPROTOCOL_STACK_EASY_PROXY_API_KEY` | Python provider easy-proxy API key |
| `EASYPROTOCOL_PROVIDER_REGISTER_R2_ACCESS_KEY_ID` | R2 upload access key id |
| `EASYPROTOCOL_PROVIDER_REGISTER_R2_SECRET_ACCESS_KEY` | R2 upload secret access key |
| `EASYPROTOCOL_PROVIDER_REGISTER_R2_ACCOUNT_ID` | R2 account id |
| `EASYPROTOCOL_PROVIDER_REGISTER_R2_BUCKET` | R2 bucket |
| `EASYPROTOCOL_PROVIDER_REGISTER_R2_ENDPOINT_URL` | optional R2 endpoint override |
| `EASYPROTOCOL_PROVIDER_REGISTER_R2_REGION` | optional R2 region override |
| `EASYPROTOCOL_PROVIDER_REGISTER_R2_PUBLIC_BASE_URL` | optional public R2 base URL |
| `EASYPROTOCOL_PROVIDER_REGISTER_OUTPUT_DIR_HOST` | local host path for register output |
| `EASYPROTOCOL_PROVIDER_REGISTER_TEAM_AUTH_DIR_HOST` | local host path for team auth files |
| `EASYPROTOCOL_PROVIDER_REGISTER_TEAM_LOCAL_DIR_HOST` | local host path for local team auth store |

## Current Workflow Set

- `validate.yml`
  - runs root repository validation
- `publish-service-base-ghcr.yml`
  - validates release tags
  - renders config
  - runs smoke
  - publishes the gateway image to GHCR
- `publish-provider-images-ghcr.yml`
  - validates provider release tags
  - materializes config
  - publishes one or all provider images to GHCR
