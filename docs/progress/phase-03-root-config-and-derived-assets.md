# Phase 3: Root Config And Derived Assets

- [x] Define the root config schema.
  Acceptance: `config.example.yaml` exists and covers gateway, providers,
  stack, and publishing sections.

- [x] Add renderers for derived deploy files.
  Acceptance: root scripts can generate module-local config artifacts from the
  root config.

- [x] Normalize imported tracked config files into templates or generated files.
  Acceptance: contributor docs tell users to edit only the root `config.yaml`.

## Notes

- Mirror the EasyEmail operator model where feasible.
- Keep generated files ignored by Git.
- Initial rendering is already validated against the example config.
- Gateway Docker and compose assets now point at the monorepo-native paths and
  generated config locations.
- Python provider config now models one manager endpoint plus dynamic pool
  sizing knobs instead of a fixed replica count.
