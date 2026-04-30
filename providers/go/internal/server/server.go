package server

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/textproto"
	"net/url"
	"os"
	"strings"
	"time"
)

type Server struct {
	addr            string
	upstreamBaseURL string
	client          *http.Client
	srv             *http.Server
}

var capabilities = []string{
	"health.inspect",
	"protocol.echo",
	"protocol.headers.normalize",
	"protocol.query.encode",
	"protocol.hash.sha256",
	"codex.register.protocol",
	"codex.repair.protocol",
	"codex.semantic.step",
}

type invokeRequest struct {
	RequestID string         `json:"request_id"`
	Mode      string         `json:"mode"`
	Operation string         `json:"operation"`
	Payload   map[string]any `json:"payload,omitempty"`
}

type serviceError struct {
	Category string         `json:"category"`
	Message  string         `json:"message"`
	Details  map[string]any `json:"details,omitempty"`
}

type upstreamInvokeResponse struct {
	RequestID string         `json:"request_id"`
	Service   string         `json:"service"`
	Status    string         `json:"status"`
	Result    map[string]any `json:"result"`
	Error     *serviceError  `json:"error"`
}

func New(addr string) *Server {
	mux := http.NewServeMux()
	upstreamBaseURL := strings.TrimRight(strings.TrimSpace(os.Getenv("GOLANG_PROTOCOL_UPSTREAM_BASE_URL")), "/")
	if upstreamBaseURL == "" {
		upstreamBaseURL = "http://127.0.0.1:9100"
	}
	s := &Server{
		addr:            addr,
		upstreamBaseURL: upstreamBaseURL,
		client: &http.Client{
			Timeout: 60 * time.Second,
		},
		srv: &http.Server{
			Addr:    addr,
			Handler: mux,
		},
	}
	mux.HandleFunc("/health", s.handleHealth)
	mux.HandleFunc("/capabilities", s.handleCapabilities)
	mux.HandleFunc("/invoke", s.handleInvoke)
	return s
}

func (s *Server) ListenAndServe() error {
	return s.srv.ListenAndServe()
}

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"service":           "GolangProtocol",
		"status":            "ok",
		"listen":            s.addr,
		"upstream_base_url": s.upstreamBaseURL,
	})
}

func (s *Server) handleCapabilities(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"service":    "GolangProtocol",
		"language":   "go",
		"operations": capabilities,
	})
}

func (s *Server) handleInvoke(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		return
	}
	var req invokeRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeFailure(w, http.StatusBadRequest, "", &serviceError{
			Category: "validation_error",
			Message:  "invalid request body",
		})
		return
	}
	if req.Payload == nil {
		req.Payload = map[string]any{}
	}
	result, svcErr := s.execute(req)
	if svcErr != nil {
		writeFailure(w, http.StatusOK, req.RequestID, svcErr)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"request_id": req.RequestID,
		"service":    "GolangProtocol",
		"status":     "succeeded",
		"result":     result,
	})
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func writeFailure(w http.ResponseWriter, status int, requestID string, err *serviceError) {
	writeJSON(w, status, map[string]any{
		"request_id": requestID,
		"service":    "GolangProtocol",
		"status":     "failed",
		"error":      err,
	})
}

func (s *Server) execute(req invokeRequest) (map[string]any, *serviceError) {
	switch req.Operation {
	case "health.inspect":
		return buildResult(req, map[string]any{
			"service":           "GolangProtocol",
			"status":            "ok",
			"listen":            s.addr,
			"upstream_base_url": s.upstreamBaseURL,
		}), nil
	case "protocol.echo":
		return buildResult(req, map[string]any{
			"echo": req.Payload,
		}), nil
	case "protocol.headers.normalize":
		headersRaw, ok := req.Payload["headers"].(map[string]any)
		if !ok {
			return nil, &serviceError{
				Category: "validation_error",
				Message:  "payload.headers must be an object",
			}
		}
		normalized := map[string]string{}
		for key, value := range headersRaw {
			normalized[textproto.CanonicalMIMEHeaderKey(strings.TrimSpace(key))] = fmt.Sprint(value)
		}
		return buildResult(req, map[string]any{
			"headers":      normalized,
			"header_count": len(normalized),
		}), nil
	case "protocol.query.encode":
		paramsRaw, ok := req.Payload["params"].(map[string]any)
		if !ok {
			return nil, &serviceError{
				Category: "validation_error",
				Message:  "payload.params must be an object",
			}
		}
		values := url.Values{}
		for key, value := range paramsRaw {
			appendQueryValues(values, key, value)
		}
		return buildResult(req, map[string]any{
			"query":       values.Encode(),
			"param_count": len(values),
			"normalized":  values,
		}), nil
	case "protocol.hash.sha256":
		text, ok := req.Payload["text"].(string)
		if !ok {
			return nil, &serviceError{
				Category: "validation_error",
				Message:  "payload.text must be a string",
			}
		}
		sum := sha256.Sum256([]byte(text))
		return buildResult(req, map[string]any{
			"text":   text,
			"digest": hex.EncodeToString(sum[:]),
		}), nil
	case "codex.register.protocol", "codex.repair.protocol", "codex.semantic.step":
		return s.forwardCodexInvoke(req)
	default:
		return nil, &serviceError{
			Category: "unsupported_operation",
			Message:  "service does not support operation",
			Details: map[string]any{
				"operation": req.Operation,
			},
		}
	}
}

func (s *Server) forwardCodexInvoke(req invokeRequest) (map[string]any, *serviceError) {
	endpoint := strings.TrimRight(s.upstreamBaseURL, "/") + "/invoke"
	body, err := json.Marshal(req)
	if err != nil {
		return nil, &serviceError{
			Category: "delegation_error",
			Message:  "failed to encode upstream request",
			Details: map[string]any{"reason": err.Error()},
		}
	}
	upstreamReq, err := http.NewRequest(http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return nil, &serviceError{
			Category: "delegation_error",
			Message:  "failed to create upstream request",
			Details: map[string]any{"reason": err.Error(), "endpoint": endpoint},
		}
	}
	upstreamReq.Header.Set("Content-Type", "application/json")
	resp, err := s.client.Do(upstreamReq)
	if err != nil {
		return nil, &serviceError{
			Category: "service_unavailable",
			Message:  "upstream protocol provider is unavailable",
			Details: map[string]any{"reason": err.Error(), "endpoint": endpoint},
		}
	}
	defer resp.Body.Close()
	payload, readErr := io.ReadAll(resp.Body)
	if readErr != nil {
		return nil, &serviceError{
			Category: "delegation_error",
			Message:  "failed to read upstream response",
			Details: map[string]any{"reason": readErr.Error(), "endpoint": endpoint},
		}
	}
	var upstream upstreamInvokeResponse
	if err := json.Unmarshal(payload, &upstream); err != nil {
		return nil, &serviceError{
			Category: "delegation_error",
			Message:  "failed to parse upstream response",
			Details: map[string]any{"reason": err.Error(), "endpoint": endpoint, "status_code": resp.StatusCode},
		}
	}
	if upstream.Error != nil || strings.EqualFold(upstream.Status, "failed") {
		if upstream.Error == nil {
			upstream.Error = &serviceError{Category: "delegation_error", Message: "upstream provider reported failure"}
		}
		if upstream.Error.Details == nil {
			upstream.Error.Details = map[string]any{}
		}
		upstream.Error.Details["upstream_service"] = upstream.Service
		upstream.Error.Details["upstream_endpoint"] = endpoint
		upstream.Error.Details["status_code"] = resp.StatusCode
		return nil, upstream.Error
	}
	result := upstream.Result
	if result == nil {
		result = map[string]any{}
	}
	result["provider_adapter"] = "GolangProtocol"
	result["adapter_language"] = "go"
	if upstream.Service != "" {
		result["upstream_service"] = upstream.Service
	}
	return result, nil
}

func buildResult(req invokeRequest, extra map[string]any) map[string]any {
	result := map[string]any{
		"language":  "go",
		"operation": req.Operation,
		"mode":      req.Mode,
	}
	for key, value := range extra {
		result[key] = value
	}
	return result
}

func appendQueryValues(values url.Values, key string, value any) {
	switch typed := value.(type) {
	case []any:
		for _, item := range typed {
			values.Add(key, fmt.Sprint(item))
		}
	default:
		values.Add(key, fmt.Sprint(value))
	}
}
