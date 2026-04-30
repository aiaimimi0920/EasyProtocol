# Risk Assessment

## High-Risk Areas

### 1. Stale documentation versus runtime reality

The legacy workspace still contains older skeleton-oriented docs, but the code
already implements real gateway and provider behavior.

Risk:

- migration decisions can be made from outdated documentation instead of code

Mitigation:

- treat runtime source files and deploy assets as the source of truth
- rewrite public monorepo docs from imported code, not placeholder docs

### 2. Runtime artifacts mixed into source trees

The Python provider contains success JSON outputs, runtime data JSON files, and
`__pycache__`. The Rust provider contains `target/` build output.

Risk:

- importing these into the public repo pollutes history
- contributors misread runtime artifacts as source fixtures

Mitigation:

- exclude caches and build output during structural import
- exclude JSON success/state artifacts that are clearly runtime byproducts

### 3. Multi-language toolchain complexity

The repository spans Go, Python, Node.js, Rust, Docker, and PowerShell.

Risk:

- root CI becomes slow or brittle
- contributor entrypoints become unclear

Mitigation:

- keep module-local toolchains explicit
- provide root scripts that orchestrate the modules instead of hiding them
- gate workflow scope by changed paths where practical

### 4. Stack assets depend on external repos and live-like services

`deploy/EasyStack` references EasyEmail and EasyProxy services and external
shared directories.

Risk:

- the imported stack can look self-contained when it is not
- hosted CI or contributors may expect it to run without the surrounding stack

Mitigation:

- preserve the stack boundary under `deploy/stacks/easy-stack`
- document external dependencies clearly
- avoid claiming that stack compose is the first public quickstart target

## Medium-Risk Areas

### 5. Root config convergence work

The source workspace currently uses deploy templates and module-local runtime
files rather than one public root operator config.

Risk:

- config sprawl survives into the public repo
- GitHub Actions and local scripts drift apart

Mitigation:

- adopt a root `config.example.yaml` early
- add renderers that generate deploy-local files from the root config
- reuse the EasyEmail pattern of `materialize-action-config.py`

### 6. Release automation is net-new

The source repo does not already have working hosted workflows.

Risk:

- workflow design takes longer than structural import
- first-pass release automation can encode the wrong paths

Mitigation:

- finish structural import before final workflow wiring
- base path gates and workflow shape on the imported monorepo layout

## Low-Risk Areas

### 7. Git history continuity

This is intentionally a copy-only migration into a fresh public repo.

Risk:

- no preserved per-module Git history inside the new monorepo

Mitigation:

- keep migration-plan mapping docs in the repo
- preserve explicit source-to-target tables for future audits

