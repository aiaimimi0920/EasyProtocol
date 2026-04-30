# RustProtocol Deploy Workspace

- image role: `rust-protocol-service`
- default listen: `0.0.0.0:9100`
- upstream env: `RUST_PROTOCOL_UPSTREAM_BASE_URL`

当前 `RustProtocol` 既保留 Rust 侧 protocol 能力，也支持把 `codex.*` 主流程操作转发给上游 `PythonProtocol` runtime，便于作为 `EasyProtocol` 的可指定 provider 接入标准节点部署。
