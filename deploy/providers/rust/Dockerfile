# syntax=docker/dockerfile:1.7

FROM rust:1.87-bookworm AS builder
WORKDIR /workspace/providers/rust

COPY providers/rust/Cargo.toml ./
COPY providers/rust/Cargo.lock ./
COPY providers/rust/src ./src
RUN cargo build --release

FROM debian:bookworm-slim AS runtime
WORKDIR /app

ENV RUST_PROTOCOL_HOST=0.0.0.0 \
    RUST_PROTOCOL_PORT=9100

COPY --from=builder /workspace/providers/rust/target/release/rustprotocol /usr/local/bin/rustprotocol
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && chmod +x /usr/local/bin/rustprotocol

EXPOSE 9100
ENTRYPOINT ["/usr/local/bin/rustprotocol"]
