# syntax=docker/dockerfile:1.7

FROM python:3.10-bookworm AS runtime
WORKDIR /app

ENV PYTHON_PROTOCOL_HOST=0.0.0.0 \
    PYTHON_PROTOCOL_PORT=9100 \
    PYTHONPATH=/app/src:/app/python_shared/src

COPY providers/python/src ./src
COPY providers/python/python_shared ./python_shared

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --fix-missing --no-install-recommends ca-certificates golang \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --retries 10 --timeout 300 boto3 curl-cffi

EXPOSE 9100
CMD ["python", "src/server.py"]
