# Phase 2: Structural Import

- [x] Import the gateway runtime into `service/base`.
  Acceptance: the Go gateway source tree exists at `service/base`.

- [x] Import provider runtimes into `providers/*`.
  Acceptance: Python, Go, JavaScript, and Rust provider trees exist under the
  target layout.

- [x] Import deploy assets into mirrored `deploy/` locations.
  Acceptance: gateway, provider, and stack deploy assets exist at the mapped
  target paths.

- [x] Remove excluded artifacts from the target repo.
  Acceptance: no source `.git`, `__pycache__`, `*.pyc`, or Rust `target/`
  remains in the target tree.

## Notes

- Runtime outputs are excluded only in the new target repo.
- Source-path to target-path mapping is recorded in `docs/migration-plan.md`.
- Structural import verification confirmed the expected gateway, provider, and
  stack entry files exist in the target tree.
