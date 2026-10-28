package qq

import (
	"context"
	"strconv"
	"sync"
)

const (
	// maxConcurrentConversations bounds how many QQ conversations call Core at once.
	maxConcurrentConversations = 4
	// conversationQueueSize bounds buffered messages per conversation.
	conversationQueueSize = 64
)

// dispatcher handles each conversation's messages in arrival order on its own
// worker, so a long turn in one conversation does not block the others.
type dispatcher struct {
	ctx    context.Context
	handle func(context.Context, map[string]any)
	slots  chan struct{}

	mu     sync.Mutex
	queues map[string]chan map[string]any
}

func newDispatcher(ctx context.Context, concurrency int, handle func(context.Context, map[string]any)) *dispatcher {
	return &dispatcher{
		ctx:    ctx,
		handle: handle,
		slots:  make(chan struct{}, concurrency),
		queues: map[string]chan map[string]any{},
	}
}

// enqueue reports false when the conversation's queue is full and the message was dropped.
func (d *dispatcher) enqueue(key string, payload map[string]any) bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	queue, ok := d.queues[key]
	if !ok {
		queue = make(chan map[string]any, conversationQueueSize)
		d.queues[key] = queue
		go d.run(key, queue)
	}
	select {
	case queue <- payload:
		return true
	default:
		return false
	}
}

func (d *dispatcher) run(key string, queue chan map[string]any) {
	for {
		// Checking for more work and retiring the worker share the lock with
		// enqueue, so a message is never left in a queue without a worker.
		d.mu.Lock()
		var payload map[string]any
		select {
		case payload = <-queue:
		default:
			delete(d.queues, key)
			d.mu.Unlock()
			return
		}
		d.mu.Unlock()

		select {
		case d.slots <- struct{}{}:
		case <-d.ctx.Done():
			d.mu.Lock()
			delete(d.queues, key)
			d.mu.Unlock()
			return
		}
		d.handle(d.ctx, payload)
		<-d.slots
	}
}

// conversationKey groups messages that must be handled in order.
func conversationKey(payload map[string]any) string {
	if payload["message_type"] == "group" {
		return "g:" + strconv.FormatInt(int64From(payload["group_id"]), 10)
	}
	return "p:" + strconv.FormatInt(int64From(payload["user_id"]), 10)
}
