# Phase 5: Operator Scripts And Deploy

- [x] Add root build, smoke, and release scripts.
  Acceptance: contributors can run supported workflows from `scripts/`.

- [x] Add a repeatable sync script from `ProtocolService`.
  Acceptance: migration replay logic and exclusions are encoded in one root
  script.

- [x] Normalize stack deploy entrypoints.
  Acceptance: imported compose assets use monorepo-native paths and documented
  config expectations.

## Notes

- Keep operator entrypoints at the repo root.
- Do not rely on the legacy workspace layout after migration.
- Local release validation now passes through `deploy-easyprotocol-release.ps1`.
- The unified root entrypoint is now `scripts/deploy-subproject.ps1`.
- Gateway and stack compose instances are now wired to the external `EasyAiMi`
  network.
- The easy-protocol stack now deploys one `python-protocol-manager` service
  instead of ten fixed Python execution containers.
- A dedicated isolated-instance deploy script now exists for safe side-by-side
  bring-up on new ports and new container names.
