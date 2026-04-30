# GolangProtocol Deploy Workspace

- image role: `golang-protocol-service`
- default listen: `0.0.0.0:9100`
- upstream env: `GOLANG_PROTOCOL_UPSTREAM_BASE_URL`

当前 `GolangProtocol` 既保留自身 Go 侧 protocol 能力，也支持把 `codex.*` 主流程操作转发给上游 `PythonProtocol` runtime，便于作为 `EasyProtocol` 的可指定 provider 接入标准节点部署。
