package client

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

type Client struct {
	BaseURL string
	HTTP    *http.Client
}

func New(baseURL string) Client {
	return Client{
		BaseURL: strings.TrimRight(baseURL, "/"),
		HTTP: &http.Client{
			Timeout: 10 * time.Minute,
		},
	}
}

type MessageRequest struct {
	SessionID string         `json:"-"`
	Message   string         `json:"message"`
	Channel   string         `json:"channel"`
	UserID    string         `json:"user_id"`
	Metadata  map[string]any `json:"metadata,omitempty"`
	Provider  string         `json:"provider,omitempty"`
}

type MessageResponse struct {
	SessionID         string `json:"session_id"`
	Response          string `json:"response"`
	Error             string `json:"error"`
	ProviderSessionID string `json:"provider_session_id"`
	RunID             string `json:"run_id"`
}

type StreamEvent struct {
	Type              string         `json:"type"`
	SessionID         string         `json:"session_id"`
	Content           string         `json:"content"`
	Error             string         `json:"error"`
	ProviderSessionID string         `json:"provider_session_id"`
	RunID             string         `json:"run_id"`
	Metadata          map[string]any `json:"metadata"`
}

type StreamEventFunc func(StreamEvent) error

func (c Client) SendMessage(ctx context.Context, req MessageRequest) (MessageResponse, error) {
	if strings.TrimSpace(req.SessionID) == "" {
		return MessageResponse{}, fmt.Errorf("session id is required")
	}
	if strings.TrimSpace(req.Message) == "" {
		return MessageResponse{}, fmt.Errorf("message is required")
	}
	if c.BaseURL == "" {
		return MessageResponse{}, fmt.Errorf("core url is required")
	}

	body, err := json.Marshal(req)
	if err != nil {
		return MessageResponse{}, err
	}
	endpoint := c.BaseURL + "/v1/sessions/" + url.PathEscape(req.SessionID) + "/messages"
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return MessageResponse{}, err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	setAuthHeader(httpReq)

	resp, err := c.httpClient().Do(httpReq)
	if err != nil {
		return MessageResponse{}, fmt.Errorf("core unavailable at %s: %w", c.BaseURL, err)
	}
	defer resp.Body.Close()

	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return MessageResponse{}, err
	}

	var decoded MessageResponse
	if err := json.Unmarshal(raw, &decoded); err != nil {
		return MessageResponse{}, fmt.Errorf("core returned non-json response (%d): %s", resp.StatusCode, string(raw))
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		if decoded.Error != "" {
			return decoded, fmt.Errorf("core returned %d: %s", resp.StatusCode, decoded.Error)
		}
		return decoded, fmt.Errorf("core returned %d: %s", resp.StatusCode, string(raw))
	}
	if decoded.Error != "" {
		return decoded, fmt.Errorf("provider error: %s", decoded.Error)
	}
	return decoded, nil
}

func (c Client) StreamMessage(ctx context.Context, req MessageRequest, onEvent StreamEventFunc) (MessageResponse, error) {
	if strings.TrimSpace(req.SessionID) == "" {
		return MessageResponse{}, fmt.Errorf("session id is required")
	}
	if strings.TrimSpace(req.Message) == "" {
		return MessageResponse{}, fmt.Errorf("message is required")
	}
	if c.BaseURL == "" {
		return MessageResponse{}, fmt.Errorf("core url is required")
	}

	body, err := json.Marshal(req)
	if err != nil {
		return MessageResponse{}, err
	}
	endpoint := c.BaseURL + "/v1/sessions/" + url.PathEscape(req.SessionID) + "/messages/stream"
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return MessageResponse{}, err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Accept", "text/event-stream")
	setAuthHeader(httpReq)

	resp, err := c.httpClient().Do(httpReq)
	if err != nil {
		return MessageResponse{}, fmt.Errorf("core unavailable at %s: %w", c.BaseURL, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		raw, readErr := io.ReadAll(resp.Body)
		if readErr != nil {
			return MessageResponse{}, readErr
		}
		var decoded MessageResponse
		if err := json.Unmarshal(raw, &decoded); err == nil && decoded.Error != "" {
			return decoded, fmt.Errorf("core returned %d: %s", resp.StatusCode, decoded.Error)
		}
		return decoded, fmt.Errorf("core returned %d: %s", resp.StatusCode, string(raw))
	}

	decoded, err := readSSE(resp.Body, onEvent)
	if err != nil {
		return decoded, err
	}
	if decoded.SessionID == "" {
		decoded.SessionID = req.SessionID
	}
	if decoded.Error != "" {
		return decoded, fmt.Errorf("provider error: %s", decoded.Error)
	}
	return decoded, nil
}

func setAuthHeader(req *http.Request) {
	if token := strings.TrimSpace(os.Getenv("ORISON_API_TOKEN")); token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
}

func (c Client) httpClient() *http.Client {
	if c.HTTP != nil {
		return c.HTTP
	}
	return http.DefaultClient
}

func readSSE(body io.Reader, onEvent StreamEventFunc) (MessageResponse, error) {
	scanner := bufio.NewScanner(body)
	scanner.Buffer(make([]byte, 0, 64*1024), 64*1024*1024)

	eventName := ""
	var dataLines []string
	response := MessageResponse{}
	var fallbackText strings.Builder

	dispatch := func() error {
		if len(dataLines) == 0 {
			eventName = ""
			return nil
		}
		currentEventName := eventName
		rawData := strings.Join(dataLines, "\n")
		eventName = ""
		dataLines = nil

		var event StreamEvent
		if err := json.Unmarshal([]byte(rawData), &event); err != nil {
			return fmt.Errorf("invalid SSE event JSON: %w", err)
		}
		if event.Type == "" {
			event.Type = currentEventName
		}
		if event.Type == "text_delta" {
			fallbackText.WriteString(event.Content)
		}
		if onEvent != nil {
			if err := onEvent(event); err != nil {
				return err
			}
		}
		switch event.Type {
		case "final":
			response = MessageResponse{
				SessionID:         event.SessionID,
				Response:          event.Content,
				Error:             event.Error,
				ProviderSessionID: event.ProviderSessionID,
				RunID:             event.RunID,
			}
			if response.Response == "" {
				response.Response = fallbackText.String()
			}
		case "error":
			response.SessionID = event.SessionID
			response.Error = event.Error
			if response.Error == "" {
				response.Error = event.Content
			}
			response.ProviderSessionID = event.ProviderSessionID
			response.RunID = event.RunID
		}
		return nil
	}

	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			if err := dispatch(); err != nil {
				return response, err
			}
			continue
		}
		if strings.HasPrefix(line, ":") {
			continue
		}
		if value, ok := strings.CutPrefix(line, "event:"); ok {
			eventName = strings.TrimSpace(value)
			continue
		}
		if value, ok := strings.CutPrefix(line, "data:"); ok {
			dataLines = append(dataLines, strings.TrimPrefix(value, " "))
		}
	}
	if err := scanner.Err(); err != nil {
		return response, err
	}
	if len(dataLines) > 0 {
		if err := dispatch(); err != nil {
			return response, err
		}
	}
	if response.Response == "" && fallbackText.Len() > 0 {
		response.Response = fallbackText.String()
	}
	return response, nil
}
