use hex::encode as hex_encode;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::env;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};

const CAPABILITIES: [&str; 8] = [
    "health.inspect",
    "protocol.echo",
    "protocol.hash.sha256",
    "protocol.bytes.hex",
    "protocol.bytes.xor",
    "codex.register.protocol",
    "codex.repair.protocol",
    "codex.semantic.step",
];

#[derive(Deserialize, Serialize, Default)]
struct InvokeRequest {
    #[serde(default)]
    request_id: String,
    #[serde(default)]
    mode: String,
    #[serde(default)]
    operation: String,
    #[serde(default)]
    payload: Value,
}

#[derive(Deserialize, Default)]
struct UpstreamInvokeResponse {
    #[serde(default)]
    service: String,
    #[serde(default)]
    status: String,
    #[serde(default)]
    result: Value,
    #[serde(default)]
    error: Value,
}

struct ServiceError {
    category: String,
    message: String,
    details: Value,
}

fn main() {
    let host = env::var("RUST_PROTOCOL_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
    let port = env::var("RUST_PROTOCOL_PORT").unwrap_or_else(|_| "11004".to_string());
    let addr = format!("{}:{}", host, port);
    let listener = TcpListener::bind(&addr).expect("failed to bind RustProtocol listener");
    println!("RustProtocol listening on {}", addr);

    for stream in listener.incoming() {
        if let Ok(stream) = stream {
            handle_stream(stream, &addr);
        }
    }
}

fn handle_stream(mut stream: TcpStream, addr: &str) {
    let mut buffer = [0_u8; 65536];
    let read = match stream.read(&mut buffer) {
        Ok(size) => size,
        Err(_) => return,
    };

    let request = String::from_utf8_lossy(&buffer[..read]);
    let first_line = request.lines().next().unwrap_or("");

    if first_line.starts_with("GET /health ") {
        write_json(
            &mut stream,
            200,
            &json!({
                "service": "RustProtocol",
                "status": "ok",
                "listen": addr,
                "upstream_base_url": upstream_base_url(),
            })
            .to_string(),
        );
        return;
    }

    if first_line.starts_with("GET /capabilities ") {
        write_json(
            &mut stream,
            200,
            &json!({
                "service": "RustProtocol",
                "language": "rust",
                "operations": CAPABILITIES,
            })
            .to_string(),
        );
        return;
    }

    if first_line.starts_with("POST /invoke ") {
        let body = request
            .split_once("\r\n\r\n")
            .map(|(_, body)| body)
            .unwrap_or("{}");
        let parsed = match serde_json::from_str::<InvokeRequest>(body) {
            Ok(parsed) => parsed,
            Err(_) => {
                write_json(
                    &mut stream,
                    400,
                    &failed_response(
                        "",
                        ServiceError {
                            category: "validation_error".to_string(),
                            message: "invalid request body".to_string(),
                            details: json!({}),
                        },
                    ),
                );
                return;
            }
        };

        match execute(&parsed, addr) {
            Ok(result) => {
                write_json(
                    &mut stream,
                    200,
                    &json!({
                        "request_id": parsed.request_id,
                        "service": "RustProtocol",
                        "status": "succeeded",
                        "result": result,
                    })
                    .to_string(),
                );
            }
            Err(err) => {
                write_json(&mut stream, 200, &failed_response(&parsed.request_id, err));
            }
        }
        return;
    }

    write_json(&mut stream, 404, "{\"error\":\"not found\"}");
}

fn execute(req: &InvokeRequest, addr: &str) -> Result<Value, ServiceError> {
    match req.operation.as_str() {
        "health.inspect" => Ok(build_result(
            req,
            json!({
                "service": "RustProtocol",
                "status": "ok",
                "listen": addr,
                "upstream_base_url": upstream_base_url(),
            }),
        )),
        "protocol.echo" => Ok(build_result(req, json!({ "echo": req.payload.clone() }))),
        "protocol.hash.sha256" => {
            let text = string_field(&req.payload, "text")?;
            let digest = Sha256::digest(text.as_bytes());
            Ok(build_result(
                req,
                json!({
                    "text": text,
                    "digest": hex_encode(digest),
                }),
            ))
        }
        "protocol.bytes.hex" => {
            let text = string_field(&req.payload, "text")?;
            Ok(build_result(
                req,
                json!({
                    "text": text,
                    "hex": hex_encode(text.as_bytes()),
                }),
            ))
        }
        "protocol.bytes.xor" => {
            let left = string_field(&req.payload, "left")?;
            let right = string_field(&req.payload, "right")?;
            if left.len() != right.len() {
                return Err(ServiceError {
                    category: "validation_error".to_string(),
                    message: "payload.left and payload.right must have the same length".to_string(),
                    details: json!({}),
                });
            }
            let output: Vec<u8> = left
                .as_bytes()
                .iter()
                .zip(right.as_bytes().iter())
                .map(|(left, right)| left ^ right)
                .collect();
            Ok(build_result(
                req,
                json!({
                    "left": left,
                    "right": right,
                    "xor_hex": hex_encode(output),
                }),
            ))
        }
        "codex.register.protocol" | "codex.repair.protocol" | "codex.semantic.step" => forward_codex_invoke(req),
        _ => Err(ServiceError {
            category: "unsupported_operation".to_string(),
            message: "service does not support operation".to_string(),
            details: json!({
                "operation": req.operation,
            }),
        }),
    }
}

fn string_field(payload: &Value, key: &str) -> Result<String, ServiceError> {
    payload
        .get(key)
        .and_then(Value::as_str)
        .map(ToString::to_string)
        .ok_or_else(|| ServiceError {
            category: "validation_error".to_string(),
            message: format!("payload.{} must be a string", key),
            details: json!({}),
        })
}

fn build_result(req: &InvokeRequest, extra: Value) -> Value {
    let mut result = Map::new();
    result.insert("language".to_string(), json!("rust"));
    result.insert("operation".to_string(), json!(req.operation));
    result.insert("mode".to_string(), json!(req.mode));
    if let Value::Object(extra_map) = extra {
        for (key, value) in extra_map {
            result.insert(key, value);
        }
    }
    Value::Object(result)
}

fn upstream_base_url() -> String {
    let raw = env::var("RUST_PROTOCOL_UPSTREAM_BASE_URL").unwrap_or_else(|_| "http://127.0.0.1:9100".to_string());
    raw.trim().trim_end_matches('/').to_string()
}

fn parse_http_endpoint(endpoint: &str) -> Result<(String, u16, String), ServiceError> {
    let trimmed = endpoint.trim();
    let without_scheme = trimmed.strip_prefix("http://").ok_or_else(|| ServiceError {
        category: "delegation_error".to_string(),
        message: "only http upstream endpoints are supported".to_string(),
        details: json!({ "endpoint": endpoint }),
    })?;
    let mut parts = without_scheme.splitn(2, '/');
    let host_port = parts.next().unwrap_or("");
    let path_suffix = parts.next().unwrap_or("");
    let path = if path_suffix.is_empty() {
        "/".to_string()
    } else {
        format!("/{}", path_suffix)
    };
    let mut host = host_port.to_string();
    let mut port = 80_u16;
    if let Some((host_part, port_part)) = host_port.rsplit_once(':') {
        if let Ok(parsed_port) = port_part.parse::<u16>() {
            host = host_part.to_string();
            port = parsed_port;
        }
    }
    if host.trim().is_empty() {
        return Err(ServiceError {
            category: "delegation_error".to_string(),
            message: "upstream endpoint host is empty".to_string(),
            details: json!({ "endpoint": endpoint }),
        });
    }
    Ok((host, port, path))
}

fn forward_codex_invoke(req: &InvokeRequest) -> Result<Value, ServiceError> {
    let endpoint = format!("{}/invoke", upstream_base_url());
    let (host, port, path) = parse_http_endpoint(&endpoint)?;
    let address = format!("{}:{}", host, port);
    let mut stream = TcpStream::connect(&address).map_err(|err| ServiceError {
        category: "service_unavailable".to_string(),
        message: "upstream protocol provider is unavailable".to_string(),
        details: json!({ "endpoint": endpoint, "reason": err.to_string() }),
    })?;

    let body = serde_json::to_string(req).map_err(|err| ServiceError {
        category: "delegation_error".to_string(),
        message: "failed to encode upstream request".to_string(),
        details: json!({ "endpoint": endpoint, "reason": err.to_string() }),
    })?;

    let request = format!(
        "POST {} HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        path,
        host,
        body.as_bytes().len(),
        body,
    );
    stream.write_all(request.as_bytes()).map_err(|err| ServiceError {
        category: "delegation_error".to_string(),
        message: "failed to write upstream request".to_string(),
        details: json!({ "endpoint": endpoint, "reason": err.to_string() }),
    })?;

    let mut response = String::new();
    stream.read_to_string(&mut response).map_err(|err| ServiceError {
        category: "delegation_error".to_string(),
        message: "failed to read upstream response".to_string(),
        details: json!({ "endpoint": endpoint, "reason": err.to_string() }),
    })?;

    let (status_code, response_body) = parse_http_response(&response).ok_or_else(|| ServiceError {
        category: "delegation_error".to_string(),
        message: "failed to parse upstream HTTP response".to_string(),
        details: json!({ "endpoint": endpoint }),
    })?;

    let parsed: UpstreamInvokeResponse = serde_json::from_str(response_body).map_err(|err| ServiceError {
        category: "delegation_error".to_string(),
        message: "failed to parse upstream JSON response".to_string(),
        details: json!({ "endpoint": endpoint, "status_code": status_code, "reason": err.to_string() }),
    })?;

    if parsed.status.eq_ignore_ascii_case("failed") || parsed.error.is_object() {
        let (category, message, mut details) = service_error_from_value(&parsed.error, "delegation_error", "upstream provider reported failure");
        if !details.is_object() {
            details = json!({});
        }
        if let Some(details_map) = details.as_object_mut() {
            details_map.insert("upstream_service".to_string(), json!(parsed.service));
            details_map.insert("upstream_endpoint".to_string(), json!(endpoint));
            details_map.insert("status_code".to_string(), json!(status_code));
        }
        return Err(ServiceError { category, message, details });
    }

    if status_code >= 400 {
        return Err(ServiceError {
            category: "delegation_error".to_string(),
            message: "upstream provider returned HTTP error".to_string(),
            details: json!({ "endpoint": endpoint, "status_code": status_code }),
        });
    }

    let mut result = match parsed.result {
        Value::Object(map) => map,
        _ => Map::new(),
    };
    result.insert("provider_adapter".to_string(), json!("RustProtocol"));
    result.insert("adapter_language".to_string(), json!("rust"));
    if !parsed.service.trim().is_empty() {
        result.insert("upstream_service".to_string(), json!(parsed.service));
    }
    Ok(Value::Object(result))
}

fn parse_http_response(response: &str) -> Option<(u16, &str)> {
    let mut sections = response.splitn(2, "\r\n\r\n");
    let headers = sections.next()?;
    let body = sections.next().unwrap_or("");
    let status_line = headers.lines().next().unwrap_or("");
    let status_code = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|value| value.parse::<u16>().ok())?;
    Some((status_code, body))
}

fn service_error_from_value(value: &Value, fallback_category: &str, fallback_message: &str) -> (String, String, Value) {
    let category = value
        .get("category")
        .and_then(Value::as_str)
        .unwrap_or(fallback_category)
        .to_string();
    let message = value
        .get("message")
        .and_then(Value::as_str)
        .unwrap_or(fallback_message)
        .to_string();
    let details = value.get("details").cloned().unwrap_or_else(|| json!({}));
    (category, message, details)
}

fn failed_response(request_id: &str, err: ServiceError) -> String {
    json!({
        "request_id": request_id,
        "service": "RustProtocol",
        "status": "failed",
        "error": {
            "category": err.category,
            "message": err.message,
            "details": err.details,
        },
    })
    .to_string()
}

fn write_json(stream: &mut TcpStream, status: u16, body: &str) {
    let status_line = match status {
        200 => "HTTP/1.1 200 OK",
        400 => "HTTP/1.1 400 Bad Request",
        404 => "HTTP/1.1 404 Not Found",
        405 => "HTTP/1.1 405 Method Not Allowed",
        _ => "HTTP/1.1 500 Internal Server Error",
    };

    let response = format!(
        "{status_line}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        body.as_bytes().len(),
        body
    );
    let _ = stream.write_all(response.as_bytes());
}
