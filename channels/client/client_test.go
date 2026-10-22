package client

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSendMessagePostsToCoreAndParsesFinalResponse(t *testing.T) {
	var gotPath string
	var gotPayload map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		if err := json.NewDecoder(r.Body).Decode(&gotPayload); err != nil {
			t.Fatal(err)
		}
		_ = json.NewEncoder(w).Encode(MessageResponse{
			SessionID:         "qq:42",
			Response:          "final text",
			ProviderSessionID: "thread-1",
			RunID:             "run-1",
		})
	}))
	defer server.Close()

	resp, err := New(server.URL).SendMessage(context.Background(), MessageRequest{
		SessionID: "qq:42",
		Message:   "hello",
		Channel:   "qq",
		UserID:    "42",
		Metadata:  map[string]any{"message_type": "private"},
	})
	if err != nil {
		t.Fatalf("SendMessage returned error: %v", err)
	}
	if gotPath != "/v1/sessions/qq:42/messages" {
		t.Fatalf("unexpected path: %s", gotPath)
	}
	if gotPayload["message"] != "hello" || gotPayload["channel"] != "qq" || gotPayload["user_id"] != "42" {
		t.Fatalf("unexpected payload: %#v", gotPayload)
	}
	if resp.Response != "final text" || resp.ProviderSessionID != "thread-1" {
		t.Fatalf("unexpected response: %#v", resp)
	}
}

func TestSendMessageReturnsClearCoreErrors(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
		_ = json.NewEncoder(w).Encode(map[string]any{"error": "core down"})
	}))
	defer server.Close()

	_, err := New(server.URL).SendMessage(context.Background(), MessageRequest{
		SessionID: "s",
		Message:   "hello",
	})
	if err == nil {
		t.Fatal("expected error")
	}
}

func TestStreamMessageParsesSSEEventsAndFinalResponse(t *testing.T) {
	var gotPath string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		w.Header().Set("Content-Type", "text/event-stream")
		writeSSE(t, w, "session_started", StreamEvent{
			Type:              "session_started",
			SessionID:         "local:test",
			ProviderSessionID: "thread-1",
		})
		writeSSE(t, w, "text_delta", StreamEvent{
			Type:      "text_delta",
			SessionID: "local:test",
			Content:   "hello ",
		})
		writeSSE(t, w, "text_delta", StreamEvent{
			Type:      "text_delta",
			SessionID: "local:test",
			Content:   "world",
		})
		writeSSE(t, w, "final", StreamEvent{
			Type:              "final",
			SessionID:         "local:test",
			Content:           "hello world",
			ProviderSessionID: "thread-1",
			RunID:             "run-1",
		})
	}))
	defer server.Close()

	var eventTypes []string
	resp, err := New(server.URL).StreamMessage(context.Background(), MessageRequest{
		SessionID: "local:test",
		Message:   "hello",
	}, func(event StreamEvent) error {
		eventTypes = append(eventTypes, event.Type)
		return nil
	})
	if err != nil {
		t.Fatalf("StreamMessage returned error: %v", err)
	}
	if gotPath != "/v1/sessions/local:test/messages/stream" {
		t.Fatalf("unexpected path: %s", gotPath)
	}
	if resp.Response != "hello world" || resp.ProviderSessionID != "thread-1" || resp.RunID != "run-1" {
		t.Fatalf("unexpected response: %#v", resp)
	}
	want := []string{"session_started", "text_delta", "text_delta", "final"}
	if len(eventTypes) != len(want) {
		t.Fatalf("unexpected events: %#v", eventTypes)
	}
	for i := range want {
		if eventTypes[i] != want[i] {
			t.Fatalf("unexpected events: %#v", eventTypes)
		}
	}
}

func TestStreamMessageReturnsProviderErrors(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		writeSSE(t, w, "error", StreamEvent{
			Type:    "error",
			Content: "provider down",
			Error:   "provider down",
		})
	}))
	defer server.Close()

	resp, err := New(server.URL).StreamMessage(context.Background(), MessageRequest{
		SessionID: "s",
		Message:   "hello",
	}, nil)
	if err == nil {
		t.Fatal("expected error")
	}
	if resp.Error != "provider down" {
		t.Fatalf("unexpected response: %#v", resp)
	}
}

func TestStreamMessageAcceptsLargeImageFinalEvent(t *testing.T) {
	largeImage := "data:image/png;base64," + strings.Repeat("A", 2*1024*1024)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		writeSSE(t, w, "final", StreamEvent{
			Type:      "final",
			SessionID: "local:test",
			Content:   "生成好了",
			Metadata:  map[string]any{"images": []any{map[string]any{"data": largeImage}}},
		})
	}))
	defer server.Close()

	resp, err := New(server.URL).StreamMessage(context.Background(), MessageRequest{
		SessionID: "local:test",
		Message:   "hello",
	}, nil)
	if err != nil {
		t.Fatalf("StreamMessage returned error for large image event: %v", err)
	}
	if resp.Response != "生成好了" {
		t.Fatalf("unexpected response: %#v", resp)
	}
}

func writeSSE(t *testing.T, w http.ResponseWriter, eventName string, event StreamEvent) {
	t.Helper()
	raw, err := json.Marshal(event)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := w.Write([]byte("event: " + eventName + "\n")); err != nil {
		t.Fatal(err)
	}
	if _, err := w.Write([]byte("data: " + string(raw) + "\n\n")); err != nil {
		t.Fatal(err)
	}
}

func TestSendMessageForwardsObservationFlag(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Error(err)
		}
		if payload["observe_only"] != true {
			t.Error("observation flag was not forwarded")
		}
		_ = json.NewEncoder(w).Encode(MessageResponse{SessionID: "qq:g:303", Response: ""})
	}))
	defer server.Close()
	_, err := New(server.URL).SendMessage(context.Background(), MessageRequest{
		SessionID: "qq:g:303", Message: "Family chat", ObserveOnly: true,
	})
	if err != nil {
		t.Fatal(err)
	}
}
