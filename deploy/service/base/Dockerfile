# syntax=docker/dockerfile:1.7

FROM golang:1.26-bookworm AS builder
WORKDIR /workspace

COPY service/base/go.mod service/base/go.sum ./service/base/
WORKDIR /workspace/service/base
RUN go mod download

COPY service/base ./
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/easy_protocol ./cmd/easy_protocol

FROM debian:bookworm-slim AS runtime
WORKDIR /app

ENV EASY_PROTOCOL_CONFIG_PATH=/etc/easy-protocol/config.yaml \
    EASY_PROTOCOL_RUNTIME_ENV_PATH=/etc/easy-protocol/runtime.env \
    EASY_PROTOCOL_STATE_DIR=/var/lib/easy-protocol

COPY --from=builder /out/easy_protocol /usr/local/bin/easy_protocol
COPY deploy/service/base/config.template.yaml /opt/easy-protocol/config.template.yaml
COPY deploy/service/base/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY deploy/service/base/bootstrap-service-config.py /usr/local/bin/bootstrap-service-config.py
COPY scripts/easyprotocol-import-code.py /usr/local/bin/easyprotocol-import-code.py

RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --fix-missing --no-install-recommends ca-certificates gosu python3 python3-pip \
    && python3 -m pip install --no-cache-dir --break-system-packages boto3 pynacl \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -u 10001 easy \
    && sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh /usr/local/bin/easy_protocol /usr/local/bin/bootstrap-service-config.py /usr/local/bin/easyprotocol-import-code.py \
    && mkdir -p /etc/easy-protocol /var/lib/easy-protocol /var/lib/easy-protocol/state \
    && chown -R easy:easy /etc/easy-protocol /var/lib/easy-protocol /opt/easy-protocol

EXPOSE 9788
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
