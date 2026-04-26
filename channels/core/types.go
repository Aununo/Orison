package core

import "context"

type Message struct {
	SessionID string
	Channel   string
	UserID    string
	Text      string
	Metadata  map[string]any
	Reply     ReplyFunc
	Stream    StreamReplyFunc
}

type Event struct {
	Type      string
	SessionID string
	UserID    string
	Text      string
	Metadata  map[string]any
}

type ReplyFunc func(ctx context.Context, text string) error

type StreamReplyFunc func(ctx context.Context, event Event) error

type Handler interface {
	HandleMessage(ctx context.Context, msg Message) error
}

type HandlerFunc func(ctx context.Context, msg Message) error

func (f HandlerFunc) HandleMessage(ctx context.Context, msg Message) error {
	return f(ctx, msg)
}

type Channel interface {
	Name() string
	Run(ctx context.Context, handler Handler) error
}
