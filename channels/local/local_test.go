package local

import (
	"bytes"
	"context"
	"testing"

	"orison/channels/core"
)

func TestLocalChannelUsesStableSessionAndReplies(t *testing.T) {
	t.Setenv("ORISON_SESSION_ID", "local:test")
	t.Setenv("ORISON_USER_ID", "user:test")

	var out bytes.Buffer
	channel := New(core.Config{
		Channel: core.ChannelConfig{CoreURL: "http://127.0.0.1:8765"},
	})
	channel.In = bytes.NewBufferString("hello\n/quit\n")
	channel.Out = &out

	handled := make(chan core.Message, 1)
	err := channel.Run(context.Background(), core.HandlerFunc(func(ctx context.Context, msg core.Message) error {
		handled <- msg
		return msg.Reply(ctx, "reply")
	}))
	if err != nil {
		t.Fatal(err)
	}

	msg := <-handled
	if msg.SessionID != "local:test" || msg.Channel != "local" || msg.UserID != "user:test" || msg.Text != "hello" {
		t.Fatalf("unexpected message: %#v", msg)
	}
	if !bytes.Contains(out.Bytes(), []byte("reply")) {
		t.Fatalf("reply not printed: %q", out.String())
	}
}

func TestLocalChannelStreamsDeltasWithoutDuplicatingFinal(t *testing.T) {
	t.Setenv("ORISON_SESSION_ID", "local:test")
	t.Setenv("ORISON_USER_ID", "user:test")

	var out bytes.Buffer
	channel := New(core.Config{
		Channel: core.ChannelConfig{CoreURL: "http://127.0.0.1:8765"},
	})
	channel.In = bytes.NewBufferString("hello\n/quit\n")
	channel.Out = &out

	err := channel.Run(context.Background(), core.HandlerFunc(func(ctx context.Context, msg core.Message) error {
		if msg.Stream == nil {
			t.Fatal("stream callback is not configured")
		}
		if err := msg.Stream(ctx, core.Event{Type: "text_delta", Text: "hello "}); err != nil {
			return err
		}
		if err := msg.Stream(ctx, core.Event{Type: "text_delta", Text: "world"}); err != nil {
			return err
		}
		return msg.Stream(ctx, core.Event{Type: "final", Text: "hello world"})
	}))
	if err != nil {
		t.Fatal(err)
	}

	if count := bytes.Count(out.Bytes(), []byte("hello world")); count != 1 {
		t.Fatalf("final response duplicated or missing (%d): %q", count, out.String())
	}
}

func TestCompactKeepsUTF8Valid(t *testing.T) {
	if got := compact("工具调用结果非常长需要截断", 6); got != "工具调..." {
		t.Fatalf("unexpected compact text: %q", got)
	}
}
