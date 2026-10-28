package local

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"strings"

	"orison/channels/core"
)

type Channel struct {
	Config core.Config
	In     io.Reader
	Out    io.Writer
}

func New(config core.Config) Channel {
	return Channel{Config: config, In: os.Stdin, Out: os.Stdout}
}

func (c Channel) Name() string { return "local" }

func (c Channel) Run(ctx context.Context, handler core.Handler) error {
	in := c.In
	if in == nil {
		in = os.Stdin
	}
	out := c.Out
	if out == nil {
		out = os.Stdout
	}

	sessionID := os.Getenv("ORISON_SESSION_ID")
	if sessionID == "" {
		sessionID = "local:default"
	}
	userID := os.Getenv("ORISON_USER_ID")
	if userID == "" {
		userID = "local-user"
	}

	fmt.Fprintf(out, "Orison local channel ready. Core=%s session=%s\n", c.Config.Channel.CoreURL, sessionID)
	scanner := bufio.NewScanner(in)
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		if !scanner.Scan() {
			if err := scanner.Err(); err != nil {
				return err
			}
			return nil
		}
		text := strings.TrimSpace(scanner.Text())
		if text == "" {
			continue
		}
		if strings.EqualFold(text, "/quit") || strings.EqualFold(text, "/exit") {
			return nil
		}

		streamPrinted := false
		streamEndedWithNewline := true
		msg := core.Message{
			SessionID: sessionID,
			Channel:   "local",
			UserID:    userID,
			Text:      text,
			Metadata: map[string]any{
				"source": "terminal",
			},
			Reply: func(ctx context.Context, reply string) error {
				_, err := fmt.Fprintln(out, reply)
				return err
			},
			Stream: func(ctx context.Context, event core.Event) error {
				switch event.Type {
				case "text_delta":
					if event.Text == "" {
						return nil
					}
					streamPrinted = true
					streamEndedWithNewline = strings.HasSuffix(event.Text, "\n")
					_, err := fmt.Fprint(out, event.Text)
					return err
				case "status":
					if event.Text == "" {
						return nil
					}
					_, err := fmt.Fprintf(out, "\n[status] %s\n", event.Text)
					streamEndedWithNewline = true
					return err
				case "tool_use":
					_, err := fmt.Fprintf(out, "\n[tool] %s\n", compactEventText(event))
					streamEndedWithNewline = true
					return err
				case "tool_result":
					if event.Text == "" {
						return nil
					}
					_, err := fmt.Fprintf(out, "\n[tool result] %s\n", compact(event.Text, 240))
					streamEndedWithNewline = true
					return err
				case "error":
					_, err := fmt.Fprintf(out, "\n[error] %s\n", event.Text)
					streamEndedWithNewline = true
					return err
				case "final":
					if !streamPrinted && event.Text != "" {
						_, err := fmt.Fprintln(out, event.Text)
						streamEndedWithNewline = true
						return err
					}
					if streamPrinted && !streamEndedWithNewline {
						_, err := fmt.Fprintln(out)
						streamEndedWithNewline = true
						return err
					}
				}
				return nil
			},
		}
		if err := handler.HandleMessage(ctx, msg); err != nil {
			fmt.Fprintf(out, "error: %v\n", err)
		}
	}
}

func compactEventText(event core.Event) string {
	if event.Text != "" {
		return compact(event.Text, 160)
	}
	if name, ok := event.Metadata["tool_name"].(string); ok && name != "" {
		return name
	}
	return event.Type
}

// compact limits text to limit characters without splitting a UTF-8 sequence.
func compact(text string, limit int) string {
	text = strings.TrimSpace(text)
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	if limit <= 3 {
		return string(runes[:limit])
	}
	return string(runes[:limit-3]) + "..."
}
