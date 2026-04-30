# Task Breakdown

## Phase 1: Monorepo Bootstrap

### Lane A

- [ ] Create the empty target repo skeleton
  Acceptance: root directories exist for `service`, `providers`, `deploy`,
  `docs`, `scripts`, and `.github/workflows`.

- [ ] Add root bootstrap docs and ignore rules
  Acceptance: root README, `.gitignore`, migration plan, and progress docs are
  committed in the new repo.

### Lane B

- [ ] Capture source analysis and migration mapping
  Acceptance: `docs/analysis/*` and `docs/plan/*` describe the source layout,
  target mapping, and guardrails.

## Phase 2: Structural Import

### Lane A

- [ ] Import `repos/EasyProtocol` into `service/base`
  Acceptance: gateway source, docs, tests, and Go module files exist under
  `service/base`.

- [ ] Import provider runtimes into `providers/*`
  Acceptance: Python, Go, JavaScript, and Rust providers exist under the new
  target paths.

### Lane B

- [ ] Import deploy assets into mirrored `deploy/` locations
  Acceptance: gateway, provider, and stack deploy assets exist under
  `deploy/service/base`, `deploy/providers/*`, and `deploy/stacks/easy-stack`.

- [ ] Remove excluded runtime artifacts from the imported target tree
  Acceptance: no `.git`, `__pycache__`, `*.pyc`, or `providers/rust/target`
  content remains in the target repo.

## Phase 3: Root Config And Derived Assets

### Lane A

- [ ] Define root config schema
  Acceptance: `config.example.yaml` exists with sections for gateway, providers,
  stack deployment, and publishing.

- [ ] Add derived-config renderers
  Acceptance: root scripts can generate gateway and stack-local config files
  from the root config.

### Lane B

- [ ] Normalize imported tracked config files into templates
  Acceptance: public docs and scripts point contributors at the root config
  instead of editing imported per-module runtime files directly.

## Phase 4: CI / GHCR / Release Automation

### Lane A

- [ ] Add repository validation workflow
  Acceptance: `.github/workflows/validate.yml` installs the required toolchains
  and runs the repository validation entrypoint.

- [ ] Add GHCR publish workflow(s)
  Acceptance: hosted workflows can build and push public images for the
  intended release targets.

### Lane B

- [ ] Add GitHub Actions config materialization
  Acceptance: granular secrets can be materialized into a temporary root
  `config.yaml` in CI.

- [ ] Add release metadata, tag validation, and notes generation
  Acceptance: release workflows emit manifests, validate tags, and generate
  release notes artifacts.

## Phase 5: Operator Scripts And Deploy Normalization

### Lane A

- [ ] Add root build / smoke / release scripts
  Acceptance: contributors have root entrypoints for build, smoke, and release
  flows.

- [ ] Add repeatable sync script from `ProtocolService`
  Acceptance: a root script can replay the copy-based migration with encoded
  exclusions.

### Lane B

- [ ] Normalize stack deploy entrypoints
  Acceptance: stack deploy helpers use monorepo-native paths and documented
  config inputs.

## Phase 6: Verification And Documentation

### Lane A

- [ ] Verify imported layout and exclusions
  Acceptance: verification scripts confirm expected paths exist and excluded
  artifacts do not.

- [ ] Write public contributor docs
  Acceptance: quickstart, config, GitHub secrets, and release docs exist at the
  repo root docs level.

### Lane B

- [ ] Add repository-wide validation entrypoint
  Acceptance: one root script can run the supported validation suite across the
  monorepo.

