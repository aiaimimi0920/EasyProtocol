# PythonProtocol

`PythonProtocol` is the Python-side protocol provider inside the public
`EasyProtocol` monorepo.

Current hot path implementation includes:

- `codex.semantic.step`
- `create_openai_account`
- `invite_codex_member`
- `obtain_codex_oauth`
- `revoke_codex_member`
- `upload_file_to_r2`

The provider now runs as `python-protocol-manager` and owns the Python-side
protocol execution closure that used to be embedded directly inside
`RegisterService`.

Current boundary:

- `RegisterService` keeps DST flow + scheduler
- `RegisterService` calls `EasyProtocol`
- `EasyProtocol` dispatches to `PythonProtocol`
- `PythonProtocol` executes the Python protocol runtime through a bounded
  dynamic subprocess pool

## Runtime Pool

The manager exposes runtime pool state through:

- `GET /health`
- `GET /capabilities`

See [python-protocol-manager-runtime-pool.md](../../docs/python-protocol-manager-runtime-pool.md)
for the current pool fields and control knobs.

`upload_file_to_r2` is exposed as a semantic step through `codex.semantic.step`.
The first implementation lives in `PythonProtocol`, but callers should still
invoke `EasyProtocol` rather than binding themselves to `PythonProtocol`.

Team credential discovery supports environment-driven deployment:

- `REGISTER_TEAM_AUTH_PATH`
- `REGISTER_TEAM_AUTH_DIR`
- `REGISTER_TEAM_AUTH_DIRS`
- `REGISTER_TEAM_AUTH_GLOB`

If none are set, PythonProtocol falls back to searching the user credential
directory:

- `~/.cli-proxy-api`

R2 upload configuration supports payload-first inputs with environment fallbacks:

- `REGISTER_R2_ACCESS_KEY_ID`
- `REGISTER_R2_SECRET_ACCESS_KEY`
- `REGISTER_R2_ACCOUNT_ID`
- `REGISTER_R2_BUCKET`
- `REGISTER_R2_ENDPOINT_URL`
- `REGISTER_R2_REGION`
- `REGISTER_R2_PUBLIC_BASE_URL`
