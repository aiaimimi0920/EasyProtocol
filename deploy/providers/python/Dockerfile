# syntax=docker/dockerfile:1.7

FROM python:3.10-bookworm AS runtime
WORKDIR /app

ENV PYTHON_PROTOCOL_HOST=0.0.0.0 \
    PYTHON_PROTOCOL_PORT=9100 \
    PYTHONPATH=/app/src:/app/python_shared/src:/app/python_browser_service/src \
    BROWSER_BINARY_PATH=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    HEADLESS=1 \
    USE_UNDETECTED_CHROMEDRIVER=0

COPY providers/python/src ./src
COPY providers/python/python_shared ./python_shared
COPY python_browser_service/src ./python_browser_service/src
COPY browser_runtime_requirements.txt ./browser_runtime_requirements.txt

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --fix-missing --no-install-recommends \
        ca-certificates \
        chromium \
        chromium-driver \
        fonts-liberation \
        golang \
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
        xdg-utils \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --retries 10 --timeout 300 \
        --index-url https://mirrors.aliyun.com/pypi/simple \
        --trusted-host mirrors.aliyun.com \
        -r browser_runtime_requirements.txt \
        boto3 curl-cffi

EXPOSE 9100
CMD ["python", "src/server.py"]
