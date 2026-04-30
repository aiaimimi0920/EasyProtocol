# Configuration

EasyProtocol is converging on one human-edited operator config file:

- `config.example.yaml`
- your local copy `config.yaml`

Everything else should be derived from that root file by scripts.

## Source Of Truth

Start by copying `config.example.yaml` to `config.yaml`, then edit only the
root file.

The render entrypoint is:

- `scripts/render-derived-configs.ps1`

It currently generates:

- `deploy/service/base/config/config.yaml`
- `deploy/service/base/config/runtime.env`
- `deploy/stacks/easy-protocol/generated/easy-protocol.config.yaml`
- `deploy/stacks/easy-protocol/generated/stack.env`

These generated files are ignored by Git.

## Sections

### `serviceBase`

Used to define the public gateway runtime:

- image metadata
- Dockerfile path
- runtime config overlay merged onto `deploy/service/base/config.template.yaml`

### `providers`

Used to define:

- provider image metadata
- provider registry shape for the gateway
- provider container environment defaults
- Python provider host mount paths for stack deploys

For the Python provider specifically, the container environment now also drives
the dynamic execution pool, including:

- `PYTHON_PROTOCOL_MIN_WARM_WORKERS`
- `PYTHON_PROTOCOL_MAX_WORKERS`
- `PYTHON_PROTOCOL_IDLE_TIMEOUT_SECONDS`
- `PYTHON_PROTOCOL_TASK_TIMEOUT_SECONDS`
- `PYTHON_PROTOCOL_ACQUIRE_TIMEOUT_SECONDS`
- `PYTHON_PROTOCOL_MAX_TASKS_PER_WORKER`

### `stack.easyProtocol`

Used to define:

- generated gateway config output path for the stack
- generated stack env output path
- the required external network name
- gateway published port
- Python manager published port
- external EasyEmail / EasyProxy dependency metadata

The current public-stack contract expects all compose instances to attach to
the external Docker network `EasyAiMi`.

### `publishing`

Used for hosted release automation such as GHCR publishing.

The hosted service-base publish flow now also distributes rendered runtime
artifacts through private R2. Those distribution credentials stay in GitHub
Actions repository secrets instead of tracked YAML.

## Security Rules

- never commit `config.yaml`
- never commit generated deploy-local config files
- never move live secrets into tracked example files
