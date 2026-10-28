package qq

import (
	"context"
	"sync"
	"testing"
	"time"
	"unicode/utf8"
)

func TestDispatcherDoesNotBlockOtherConversations(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	release := make(chan struct{})
	handled := make(chan string, 4)
	d := newDispatcher(ctx, maxConcurrentConversations, func(ctx context.Context, payload map[string]any) {
		key := conversationKey(payload)
		if key == "g:1" {
			<-release
		}
		handled <- key
	})

	d.enqueue("g:1", map[string]any{"message_type": "group", "group_id": float64(1)})
	d.enqueue("p:2", map[string]any{"message_type": "private", "user_id": float64(2)})
	select {
	case key := <-handled:
		if key != "p:2" {
			t.Fatalf("expected the private conversation first, got %s", key)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("a slow group turn blocked a private conversation")
	}
	close(release)
	select {
	case <-handled:
	case <-time.After(2 * time.Second):
		t.Fatal("the group message was not handled")
	}
}

func TestDispatcherKeepsConversationOrder(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var mu sync.Mutex
	var order []int
	done := make(chan struct{})
	d := newDispatcher(ctx, maxConcurrentConversations, func(ctx context.Context, payload map[string]any) {
		mu.Lock()
		defer mu.Unlock()
		order = append(order, payload["n"].(int))
		if len(order) == 20 {
			close(done)
		}
	})
	for n := 0; n < 20; n++ {
		if !d.enqueue("g:1", map[string]any{"n": n}) {
			t.Fatalf("message %d was dropped", n)
		}
	}
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("messages were not handled")
	}
	for i, n := range order {
		if i != n {
			t.Fatalf("messages were reordered: %v", order)
		}
	}
}

func TestDispatcherDropsWhenConversationQueueIsFull(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	started := make(chan struct{})
	release := make(chan struct{})
	d := newDispatcher(ctx, 1, func(ctx context.Context, payload map[string]any) {
		if payload["first"] == true {
			close(started)
		}
		<-release
	})
	defer close(release)
	d.enqueue("g:1", map[string]any{"first": true})
	<-started
	for i := 0; i < conversationQueueSize; i++ {
		if !d.enqueue("g:1", map[string]any{}) {
			t.Fatalf("message %d was dropped before the queue was full", i)
		}
	}
	if d.enqueue("g:1", map[string]any{}) {
		t.Fatal("expected a full queue to drop the message")
	}
}

func TestCompactForQQKeepsUTF8Valid(t *testing.T) {
	got := compactForQQ("提醒：明天早上九点去医院复查身体", 8)
	if !utf8.ValidString(got) || got != "提醒：明天..." {
		t.Fatalf("unexpected compact text: %q", got)
	}
	if got := compactForQQ("短文本", 8); got != "短文本" {
		t.Fatalf("short text changed: %q", got)
	}
}
