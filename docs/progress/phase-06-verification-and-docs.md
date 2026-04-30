# Phase 6: Verification And Docs

- [x] Verify the imported layout and exclusion rules.
  Acceptance: checks confirm expected target paths exist and excluded artifacts
  are absent.

- [x] Add repository-wide validation entrypoint.
  Acceptance: one root script runs the supported validation suite.

- [x] Write public contributor docs.
  Acceptance: quickstart, configuration, GitHub secrets, and release docs exist
  and use monorepo-native paths.

## Notes

- Verification should run against the new repo only.
- Public docs should describe the new single-repo contribution model.
- Local validation now covers render, Go tests, JS syntax, Python compile, and
  Rust cargo check, then cleans generated build artifacts.
- Validation now also includes a `python-protocol-manager` runtime pool smoke
  that checks `/health` pool stats and subprocess recycle behavior.
