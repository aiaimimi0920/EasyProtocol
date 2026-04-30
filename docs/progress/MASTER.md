# EasyProtocol Monorepo Migration

## Task

Copy-only migration from the legacy `ProtocolService` workspace into the new
public `EasyProtocol` monorepo, followed by root config convergence, GitHub
Actions validation, GHCR publishing, and release automation.

## Analysis Documents

- [project-overview](../analysis/project-overview.md)
- [module-inventory](../analysis/module-inventory.md)
- [risk-assessment](../analysis/risk-assessment.md)

## Plan Documents

- [migration-plan](../migration-plan.md)
- [task-breakdown](../plan/task-breakdown.md)
- [dependency-graph](../plan/dependency-graph.md)
- [milestones](../plan/milestones.md)

## Phase Summary

- [x] Phase 1: Monorepo Bootstrap (3/3 tasks) [details](./phase-01-monorepo-bootstrap.md)
- [x] Phase 2: Structural Import (4/4 tasks) [details](./phase-02-structural-import.md)
- [x] Phase 3: Root Config And Derived Assets (3/3 tasks) [details](./phase-03-root-config-and-derived-assets.md)
- [x] Phase 4: CI GHCR Release (4/4 tasks) [details](./phase-04-ci-ghcr-release.md)
- [x] Phase 5: Operator Scripts And Deploy (3/3 tasks) [details](./phase-05-operator-scripts-and-deploy.md)
- [x] Phase 6: Verification And Docs (3/3 tasks) [details](./phase-06-verification-and-docs.md)

## Current Status

Active phase: Follow-Up Review

Active task:

- preserve the migration and automation docs in-repo
- provider-image workflows are now in place
- Python provider now uses a dynamic subprocess pool behind one manager endpoint
- isolated new-instance deployment is now available without touching old containers
- review whether a remote deploy workflow is needed after target selection

## Next Steps

1. Decide whether to add a remote deploy workflow once the target runtime is fixed.
2. Optionally add provider-image smoke logic to hosted workflows.
3. Optionally reduce or reorganize migration docs after the public repo stabilizes.
