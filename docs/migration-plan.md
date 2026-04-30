# Migration Plan

## Goal

Migrate the legacy `ProtocolService` multi-repo workspace into this new
`EasyProtocol` monorepo without modifying the source workspace.

This is a copy-only migration:

- the source workspace stays untouched
- the target monorepo is reconstructed in place
- root-level submodules are avoided
- provider and deploy boundaries remain explicit

## Design Rules

1. `service/base` becomes the canonical EasyProtocol gateway runtime.
2. language-specific runtimes move into explicit self-owned boundaries under
   `providers/*`.
3. deployment assets move under `deploy/` with the same logical module split.
4. stack-level compose assets remain explicit under `deploy/stacks/*`.
5. private secrets and shared operator archives stay outside the tracked repo.
6. initial migration is structural first, then normalization and automation.

## Source To Target Mapping

The source-path column below is historical import metadata retained only to
describe the first copy migration.

| Source path | Target path | Copy rule |
| --- | --- | --- |
| `repos/EasyProtocol` | `service/base` | copy source tree, exclude `.git` |
| `repos/PythonProtocol` | `providers/python` | copy source tree, exclude `.git`, `__pycache__`, `*.pyc`, runtime output JSON artifacts |
| `repos/GolangProtocol` | `providers/go` | copy source tree, exclude `.git` |
| `repos/JSProtocol` | `providers/javascript` | copy source tree, exclude `.git` |
| `repos/RustProtocol` | `providers/rust` | copy source tree, exclude `.git`, `target/` |
| `deploy/EasyProtocol` | `deploy/service/base` | copy deploy assets, exclude local `config.yaml` |
| `deploy/PythonProtocol` | `deploy/providers/python` | copy deploy assets as baseline |
| `deploy/GolangProtocol` | `deploy/providers/go` | copy deploy assets as baseline |
| `deploy/JSProtocol` | `deploy/providers/javascript` | copy deploy assets as baseline |
| `deploy/RustProtocol` | `deploy/providers/rust` | copy deploy assets as baseline |
| `deploy/EasyStack` | `deploy/stacks/easy-protocol` | copy stack assets, exclude `.env` and `data/` |

## Public Monorepo Shape

The public repository should expose one clear root entrypoint:

- root README and quickstart
- root `config.example.yaml` and local `config.yaml`
- root scripts for build, test, release, and deploy
- root GitHub Actions for validation and GHCR publish

## Non-Goals For The Structural Import

- no mutation of the original source workspace
- no attempt to collapse all providers into one language
- no silent movement of secrets into tracked files
- no promise that imported stack files are already fully root-config-driven
- no live cutover of old Docker workloads

## Execution Phases

### Phase 1: Monorepo Bootstrap

- initialize empty `EasyProtocol` repo
- write root docs, ignore rules, and migration notes
- create target directory skeleton

### Phase 2: Structural Copy

- copy the gateway runtime into `service/base`
- copy provider runtimes into `providers/*`
- copy deploy assets into the mirrored `deploy/` structure
- preserve module-local code unless it is local runtime state or build output

### Phase 3: Root Config And Derived Assets

- design a root `config.yaml` schema
- add renderers that derive deploy configs from the root config
- stop treating per-module tracked runtime config as the operator source of truth

### Phase 4: CI / GHCR / Release Automation

- add root validation workflow
- add GHCR publish workflow(s)
- add release-tag validation and release manifest generation

### Phase 5: Operator Scripts And Deploy Normalization

- add root build, deploy, smoke, and release scripts
- add a repeatable sync script for future copy-based refreshes
- normalize stack deploy entrypoints to monorepo-native paths

### Phase 6: Verification And Documentation

- verify copied module locations exist
- verify excluded runtime artifacts were not imported
- document quickstart, config, GitHub secrets, and release flow

## Guardrails

- never write back into the legacy source workspace
- keep provider and deploy boundaries explicit
- keep runtime outputs and local state out of Git
- keep new GitHub automation rooted in this repository, not the old workspace
- do not change production-like containers unless explicitly authorized
