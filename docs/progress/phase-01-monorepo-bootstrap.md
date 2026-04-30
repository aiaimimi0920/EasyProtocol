# Phase 1: Monorepo Bootstrap

- [x] Create the target directory skeleton.
  Acceptance: `service/`, `providers/`, `deploy/`, `docs/`, `scripts/`, and
  `.github/workflows/` exist in the new repo.

- [x] Add root bootstrap docs and ignore rules.
  Acceptance: root README, `.gitignore`, and migration docs exist and describe
  the public monorepo direction.

- [x] Capture source analysis and planning docs.
  Acceptance: `docs/analysis/*`, `docs/plan/*`, and `docs/progress/*` exist and
  describe the migration path from `ProtocolService`.

## Notes

- The source workspace must remain untouched.
- The target repo is the only place where restructuring is allowed.
- The initial Git repository has been created locally and switched to `main`.
