# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS runtime
WORKDIR /app

COPY providers/javascript/package.json ./package.json
COPY providers/javascript/src ./src

ENV JS_PROTOCOL_HOST=0.0.0.0 \
    JS_PROTOCOL_PORT=9100

EXPOSE 9100
CMD ["node", "src/server.js"]
