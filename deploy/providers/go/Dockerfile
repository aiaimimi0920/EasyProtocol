# syntax=docker/dockerfile:1.7

FROM golang:1.26-bookworm AS builder
WORKDIR /workspace

COPY providers/go/go.mod ./providers/go/
WORKDIR /workspace/providers/go
RUN go mod download

COPY providers/go ./
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/golang_protocol ./cmd/golang_protocol

FROM debian:bookworm-slim AS runtime
WORKDIR /app

ENV GOLANG_PROTOCOL_LISTEN=0.0.0.0:9100

COPY --from=builder /out/golang_protocol /usr/local/bin/golang_protocol
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && chmod +x /usr/local/bin/golang_protocol

EXPOSE 9100
ENTRYPOINT ["/usr/local/bin/golang_protocol"]
