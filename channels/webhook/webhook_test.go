package webhook

import (
	"bytes"
	"context"
	"encoding/json"
	"net"
	"net/http"
	"testing"
	"time"

	"orison/channels/core"
)

func TestWebhookMessageEndpoint(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := listener.Addr().String()
	_ = listener.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	handled := make(chan core.Message, 1)
	channel := New(addr)
	go func() {
		_ = channel.Run(ctx, core.HandlerFunc(func(ctx context.Context, msg core.Message) error {
			handled <- msg
			return msg.Reply(ctx, "ok: "+msg.Text)
		}))
	}()
	time.Sleep(100 * time.Millisecond)

	body := bytes.NewBufferString(`{"user_id":"u1","text":"hello","metadata":{"source":"test"}}`)
	resp, err := http.Post("http://"+addr+"/message", "application/json", body)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	var payload map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		t.Fatal(err)
	}
	if payload["response"] != "ok: hello" || payload["session_id"] != "http:u1" {
		t.Fatalf("unexpected response: %#v", payload)
	}
	msg := <-handled
	if msg.Channel != "http" || msg.SessionID != "http:u1" || msg.UserID != "u1" {
		t.Fatalf("unexpected message: %#v", msg)
	}
}
