# Python Protocol Manager Runtime Pool

## Role

`python-protocol-manager` is the single Python provider endpoint registered in
the EasyProtocol gateway.

It does not execute all `codex.semantic.step` work inline on the HTTP request
thread. Instead, it maintains a bounded subprocess pool and hands each step to
one worker process.

## Why This Exists

The old static model kept many always-on Python execution containers. The new
manager model keeps:

- one stable provider endpoint for the gateway
- process-level isolation for task execution
- dynamic scale-up and scale-down inside the provider

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

This is intended as the main runtime pool statistics endpoint.

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
2. the manager acquires an idle worker or spawns a new one within the cap
3. the worker process executes the step
4. the result or normalized error is returned to the HTTP caller
5. the worker may be recycled if it reaches `maxTasksPerWorker`

## Pool Control Knobs

These currently come from the Python provider container environment:

- `PYTHON_PROTOCOL_MIN_WARM_WORKERS`
- `PYTHON_PROTOCOL_MAX_WORKERS`
- `PYTHON_PROTOCOL_IDLE_TIMEOUT_SECONDS`
- `PYTHON_PROTOCOL_TASK_TIMEOUT_SECONDS`
- `PYTHON_PROTOCOL_ACQUIRE_TIMEOUT_SECONDS`
- `PYTHON_PROTOCOL_MAX_TASKS_PER_WORKER`
- `PYTHON_PROTOCOL_REAPER_INTERVAL_SECONDS`

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

