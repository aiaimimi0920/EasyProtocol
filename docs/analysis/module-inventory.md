# Module Inventory

## Runtime Copy Targets

| Role | Source snapshot | Target | Notes |
| --- | --- | --- | --- |
| Unified gateway | legacy `repos/EasyProtocol` | `service/base` | canonical outward-facing runtime |
| Python provider | legacy `repos/PythonProtocol` | `providers/python` | hot-path runtime for `codex.semantic.step` and related flows |
| Go provider | legacy `repos/GolangProtocol` | `providers/go` | language-local helper runtime plus Python-forwarding adapter path |
| JavaScript provider | legacy `repos/JSProtocol` | `providers/javascript` | language-local helper runtime plus Python-forwarding adapter path |
| Rust provider | legacy `repos/RustProtocol` | `providers/rust` | language-local helper runtime plus Python-forwarding adapter path |

## Provider-Specific Notes

### `providers/python`

Important subareas retained from the source snapshot:

- `src/server.py`
- `src/new_protocol_register/*`
- `src/object_storage/*`
- `src/protocol_runtime/*`
- `python_shared/src/*`

Observed cleanup exclusions for public import:

- `__pycache__/`
- `*.pyc`
- `src/new_protocol_register/success/*.json`
- runtime state JSON files under `src/protocol_runtime/data/**/*`

### `providers/rust`

Observed cleanup exclusions for public import:

- `target/`

The source snapshot currently includes compiled build output that should not be
tracked in the public monorepo.

## Deployment Copy Targets

| Role | Source snapshot | Target | Exclusions |
| --- | --- | --- | --- |
| Gateway deploy assets | legacy `deploy/EasyProtocol` | `deploy/service/base` | exclude `config.yaml` |
| Python provider deploy assets | legacy `deploy/PythonProtocol` | `deploy/providers/python` | none for structural import |
| Go provider deploy assets | legacy `deploy/GolangProtocol` | `deploy/providers/go` | none for structural import |
| JavaScript provider deploy assets | legacy `deploy/JSProtocol` | `deploy/providers/javascript` | none for structural import |
| Rust provider deploy assets | legacy `deploy/RustProtocol` | `deploy/providers/rust` | none for structural import |
| Integration stack | legacy `deploy/EasyStack` | `deploy/stacks/easy-stack` | exclude `.env`, `data/` |

## Root-Level Assets To Create In The Target Repo

These do not have a direct source-tree equivalent and must be authored in the
target repository:

- root README
- root `.gitignore`
- root `config.example.yaml`
- root `scripts/` entrypoints
- root `.github/workflows/*`
- public contributor docs

## Private Material Boundary

The following are not part of the tracked monorepo migration:

- the shared `AIRead` archive
- local deployment secrets
- local runtime config files
- source `.git` metadata
- build output, caches, and runtime success artifacts

