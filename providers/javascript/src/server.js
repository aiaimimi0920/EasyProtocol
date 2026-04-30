const http = require("http");
const https = require("https");

const host = process.env.JS_PROTOCOL_HOST || "127.0.0.1";
const port = Number(process.env.JS_PROTOCOL_PORT || "11002");
const upstreamBaseUrl = String(process.env.JS_PROTOCOL_UPSTREAM_BASE_URL || "http://127.0.0.1:9100").trim().replace(/\/+$/u, "");
const capabilities = [
  "health.inspect",
  "protocol.echo",
  "protocol.template.render",
  "protocol.json.compact",
  "protocol.query.encode",
  "protocol.regex.extract",
  "codex.register.protocol",
  "codex.repair.protocol",
  "codex.semantic.step"
];

function writeJson(res, status, payload) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(payload));
}

function writeFailure(res, status, requestId, error) {
  writeJson(res, status, {
    request_id: requestId || "",
    service: "JSProtocol",
    status: "failed",
    error
  });
}

function buildResult(parsed, extra) {
  return {
    language: "javascript",
    operation: parsed.operation || "",
    mode: parsed.mode || "",
    ...extra
  };
}

function getObject(payload, key) {
  const value = payload?.[key];
  if (!value || Array.isArray(value) || typeof value !== "object") {
    return null;
  }
  return value;
}

function renderTemplate(template, values) {
  return template.replace(/\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}/g, (_, key) => {
    const value = values[key];
    return value === undefined || value === null ? "" : String(value);
  });
}

function extractMatches(pattern, text, flags) {
  const normalizedFlags = new Set(String(flags || "").split(""));
  normalizedFlags.add("g");
  const regex = new RegExp(pattern, Array.from(normalizedFlags).join(""));
  return Array.from(text.matchAll(regex), (match) => (match.length > 1 ? match.slice(1) : match[0]));
}

function postJson(urlString, payload) {
  return new Promise((resolve, reject) => {
    let parsedUrl;
    try {
      parsedUrl = new URL(urlString);
    } catch (error) {
      reject(error);
      return;
    }
    const transport = parsedUrl.protocol === "https:" ? https : http;
    const body = JSON.stringify(payload || {});
    const req = transport.request({
      protocol: parsedUrl.protocol,
      hostname: parsedUrl.hostname,
      port: parsedUrl.port || (parsedUrl.protocol === "https:" ? 443 : 80),
      path: `${parsedUrl.pathname}${parsedUrl.search}`,
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body)
      },
      timeout: Number(process.env.JS_PROTOCOL_UPSTREAM_TIMEOUT_MS || 60000)
    }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => {
        resolve({
          statusCode: Number(res.statusCode || 0),
          body: Buffer.concat(chunks).toString("utf8")
        });
      });
    });
    req.on("timeout", () => req.destroy(new Error("upstream request timeout")));
    req.on("error", reject);
    req.write(body);
    req.end();
  });
}

async function forwardCodexOperation(parsed) {
  const endpoint = `${upstreamBaseUrl}/invoke`;
  let upstream;
  try {
    upstream = await postJson(endpoint, parsed);
  } catch (error) {
    return {
      error: {
        category: "service_unavailable",
        message: "upstream protocol provider is unavailable",
        details: {
          endpoint,
          reason: error.message
        }
      }
    };
  }

  let payload;
  try {
    payload = upstream.body ? JSON.parse(upstream.body) : {};
  } catch (error) {
    return {
      error: {
        category: "delegation_error",
        message: "failed to parse upstream response",
        details: {
          endpoint,
          status_code: upstream.statusCode,
          reason: error.message
        }
      }
    };
  }

  if (payload?.status === "failed" || payload?.error) {
    const errorPayload = payload?.error && typeof payload.error === "object"
      ? { ...payload.error }
      : {
        category: "delegation_error",
        message: "upstream provider reported failure"
      };
    errorPayload.details = {
      ...(errorPayload.details && typeof errorPayload.details === "object" ? errorPayload.details : {}),
      upstream_service: String(payload?.service || ""),
      upstream_endpoint: endpoint,
      status_code: upstream.statusCode
    };
    return { error: errorPayload };
  }

  const result = payload?.result && typeof payload.result === "object" && !Array.isArray(payload.result)
    ? { ...payload.result }
    : {};
  result.provider_adapter = "JSProtocol";
  result.adapter_language = "javascript";
  if (payload?.service) {
    result.upstream_service = String(payload.service);
  }
  return { result };
}

async function executeOperation(parsed) {
  const payload = parsed.payload || {};

  switch (parsed.operation) {
    case "health.inspect":
      return {
        result: buildResult(parsed, {
          service: "JSProtocol",
          status: "ok",
          listen: `${host}:${port}`,
          upstream_base_url: upstreamBaseUrl
        })
      };
    case "protocol.echo":
      return {
        result: buildResult(parsed, {
          echo: payload
        })
      };
    case "protocol.template.render": {
      if (typeof payload.template !== "string") {
        return {
          error: {
            category: "validation_error",
            message: "payload.template must be a string"
          }
        };
      }
      const values = getObject(payload, "values");
      if (!values) {
        return {
          error: {
            category: "validation_error",
            message: "payload.values must be an object"
          }
        };
      }
      return {
        result: buildResult(parsed, {
          rendered: renderTemplate(payload.template, values)
        })
      };
    }
    case "protocol.json.compact":
      return {
        result: buildResult(parsed, {
          compact_json: JSON.stringify(payload.input ?? null)
        })
      };
    case "protocol.query.encode": {
      const params = getObject(payload, "params");
      if (!params) {
        return {
          error: {
            category: "validation_error",
            message: "payload.params must be an object"
          }
        };
      }
      const search = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (Array.isArray(value)) {
          for (const item of value) {
            search.append(key, String(item));
          }
        } else {
          search.append(key, String(value));
        }
      }
      return {
        result: buildResult(parsed, {
          query: search.toString(),
          param_count: Array.from(search.keys()).length
        })
      };
    }
    case "protocol.regex.extract":
      if (typeof payload.pattern !== "string" || typeof payload.text !== "string") {
        return {
          error: {
            category: "validation_error",
            message: "payload.pattern and payload.text must be strings"
          }
        };
      }
      try {
        return {
          result: buildResult(parsed, {
            matches: extractMatches(payload.pattern, payload.text, payload.flags || "")
          })
        };
      } catch (error) {
        return {
          error: {
            category: "validation_error",
            message: `invalid regex pattern: ${error.message}`
          }
        };
      }
    case "codex.register.protocol":
    case "codex.repair.protocol":
    case "codex.semantic.step":
      return forwardCodexOperation(parsed);
    default:
      return {
        error: {
          category: "unsupported_operation",
          message: "service does not support operation",
          details: {
            operation: parsed.operation || ""
          }
        }
      };
  }
}

async function handleInvoke(req, res, body) {
  let parsed = {};
  try {
    parsed = body ? JSON.parse(body) : {};
  } catch (error) {
    writeFailure(res, 400, "", {
      category: "validation_error",
      message: "invalid request body"
    });
    return;
  }

  const outcome = await executeOperation(parsed);
  if (outcome.error) {
    writeFailure(res, 200, parsed.request_id, outcome.error);
    return;
  }

  writeJson(res, 200, {
    request_id: parsed.request_id || "",
    service: "JSProtocol",
    status: "succeeded",
    result: outcome.result
  });
}

const server = http.createServer((req, res) => {
  if (req.url === "/health" && req.method === "GET") {
    writeJson(res, 200, {
      service: "JSProtocol",
      status: "ok",
      listen: `${host}:${port}`,
      upstream_base_url: upstreamBaseUrl
    });
    return;
  }

  if (req.url === "/capabilities" && req.method === "GET") {
    writeJson(res, 200, {
      service: "JSProtocol",
      language: "javascript",
      operations: capabilities
    });
    return;
  }

  if (req.url === "/invoke" && req.method === "POST") {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      handleInvoke(req, res, Buffer.concat(chunks).toString("utf8")).catch((error) => {
        writeFailure(res, 200, "", {
          category: "service_runtime_error",
          message: error.message || "unexpected invoke failure"
        });
      });
    });
    return;
  }

  writeJson(res, 404, { error: "not found" });
});

server.listen(port, host, () => {
  console.log(`JSProtocol listening on ${host}:${port}`);
});
