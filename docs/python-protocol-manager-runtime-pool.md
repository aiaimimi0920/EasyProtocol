# Python Protocol Manager Runtime Pool

## Role

`python-protocol-manager` is the single Python provider endpoint registered in
the EasyProtocol gateway.

It does not execute all `codex.semantic.step` work inline on the HTTP request
thread. Instead, it maintains a bounded subprocess pool and hands each step to
one worker process.

## Why This Exists

The old static model kept many always-on Python execution containers. The
current intended model keeps:

- one stable gateway-facing child endpoint such as `easy-protocol-python-001`
- process-level isolation for task execution inside that child
- warm capacity and replica count controlled by the EasyProtocol manager layer

## Health Endpoint

`GET /health`

Current response includes:

- `service`
- `status`
- `listen`
- `pool`

The `pool` object currently exposes:

- `mode`
- `minWarmWorkers`
- `maxWorkers`
- `idleTimeoutSeconds`
- `taskTimeoutSeconds`
- `acquireTimeoutSeconds`
- `maxTasksPerWorker`
- `totalWorkers`
- `busyWorkers`
- `idleWorkers`

This is an execution-lane health view, not the public source of truth for
top-level EasyProtocol scaling.

## Capabilities Endpoint

`GET /capabilities`

Current response includes:

- `service`
- `language`
- `operations`
- `pool`

That means operator tooling can read the configured pool envelope without
sending a real execution request.

## Execution Semantics

For `POST /invoke` with `operation=codex.semantic.step`:

1. the manager validates `step_type` and `step_input`
2. the child acquires its local execution lane
3. the worker process executes the step
4. the result or normalized error is returned to the HTTP caller
5. the worker may be recycled after local policy thresholds

## Pool Control Knobs

These currently come from the Python provider container environment, but they
should be treated as local child-lane settings rather than the main scaling
policy:

- `PYTHON_PROTOCOL_MIN_WARM_WORKERS`
- `PYTHON_PROTOCOL_MAX_WORKERS`
- `PYTHON_PROTOCOL_IDLE_TIMEOUT_SECONDS`
- `PYTHON_PROTOCOL_TASK_TIMEOUT_SECONDS`
- `PYTHON_PROTOCOL_ACQUIRE_TIMEOUT_SECONDS`
- `PYTHON_PROTOCOL_MAX_TASKS_PER_WORKER`
- `PYTHON_PROTOCOL_REAPER_INTERVAL_SECONDS`

The manager-layer warm count should instead come from the root EasyProtocol
config, for example `serviceBase.runtime.providerPool.providers.python`.

## Error Behavior

Current manager-side categories:

- `service_unavailable`
  - no worker became available before acquire timeout
- `timeout_error`
  - a worker exceeded the per-task timeout
- `service_runtime_error`
  - pool-level transport/process failures
- `operation_error`
  - the worker executed the request and the step itself rejected it

Worker-executed failures include `worker_id` inside `error.details` so a smoke
test or operator can confirm that the request really reached a subprocess.
