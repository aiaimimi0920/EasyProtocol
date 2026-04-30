# Phase 4: CI GHCR Release

- [x] Add a repository validation workflow.
  Acceptance: `.github/workflows/validate.yml` runs the supported validation
  entrypoint.

- [x] Add GHCR publish workflow(s).
  Acceptance: hosted workflows can build and push the intended public image
  targets.

- [x] Add GitHub Actions operator-config materialization.
  Acceptance: CI can synthesize a temporary root `config.yaml` from granular
  secrets.

- [x] Add release metadata, tag validation, and release notes generation.
  Acceptance: workflows emit manifests, validate tags, and upload release
  artifacts.

## Notes

- CI is net-new for this migration line.
- Follow the EasyEmail pattern of rooted workflows and release manifests.
- Hosted publish scope now covers the gateway image and provider images.
- Hosted gateway publish now also uploads rendered runtime config to private R2
  and emits an encrypted owner-only import code artifact.
