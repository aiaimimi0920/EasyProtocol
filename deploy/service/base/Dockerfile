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
    EASY_PROTOCOL_STATE_DIR=/var/lib/easy-protocol \
    BROWSER_BINARY_PATH=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    HEADLESS=1 \
    USE_UNDETECTED_CHROMEDRIVER=0

COPY --from=builder /out/easy_protocol /usr/local/bin/easy_protocol
COPY providers/python/src /opt/easy-protocol/providers/python/src
COPY providers/python/python_shared /opt/easy-protocol/providers/python/python_shared
COPY python_browser_service/src /opt/easy-protocol/python_browser_service/src
COPY browser_runtime_requirements.txt /opt/easy-protocol/browser_runtime_requirements.txt
COPY deploy/service/base/config.template.yaml /opt/easy-protocol/config.template.yaml
COPY deploy/service/base/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY deploy/service/base/bootstrap-service-config.py /usr/local/bin/bootstrap-service-config.py
COPY scripts/easyprotocol-import-code.py /usr/local/bin/easyprotocol-import-code.py
COPY scripts/patch-rendered-service-config.py /usr/local/bin/patch-rendered-service-config.py

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --fix-missing --no-install-recommends \
        ca-certificates \
        chromium \
        chromium-driver \
        fonts-liberation \
        golang \
        gosu \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libglib2.0-0 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libu2f-udev \
        libx11-6 \
        libx11-xcb1 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        python3 \
        python3-pip \
        xdg-utils \
    && python3 -m pip install --no-cache-dir --retries 10 --timeout 300 --break-system-packages \
        --index-url https://mirrors.aliyun.com/pypi/simple \
        --trusted-host mirrors.aliyun.com \
        -r /opt/easy-protocol/browser_runtime_requirements.txt \
        boto3 pynacl curl-cffi pyyaml \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -u 10001 easy \
    && sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh /usr/local/bin/easy_protocol /usr/local/bin/bootstrap-service-config.py /usr/local/bin/easyprotocol-import-code.py /usr/local/bin/patch-rendered-service-config.py \
    && mkdir -p /etc/easy-protocol /var/lib/easy-protocol /var/lib/easy-protocol/state \
    && chown -R easy:easy /etc/easy-protocol /var/lib/easy-protocol /opt/easy-protocol

EXPOSE 9788
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
