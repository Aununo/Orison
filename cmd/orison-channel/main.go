package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"orison/channels/client"
	"orison/channels/core"
	"orison/channels/local"
	"orison/channels/qq"
	"orison/channels/webhook"
)

func main() {
	configPath := os.Getenv("ORISON_CONFIG")
	if configPath == "" {
		configPath = "config.toml"
	}

	cfg, err := core.LoadConfig(configPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "load config: %v\n", err)
		os.Exit(1)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	coreClient := client.New(cfg.Channel.CoreURL)
	handler := core.HandlerFunc(func(ctx context.Context, msg core.Message) error {
		req := client.MessageRequest{
			SessionID: msg.SessionID,
			Message:   msg.Text,
			Channel:   msg.Channel,
			UserID:    msg.UserID,
			Metadata:  msg.Metadata,
		}
		if msg.Stream != nil {
			hadTextDelta := false
			hadFinal := false
			resp, err := coreClient.StreamMessage(ctx, req, func(event client.StreamEvent) error {
				if event.Type == "text_delta" && event.Content != "" {
					hadTextDelta = true
				}
				if event.Type == "final" && event.Content != "" {
					hadFinal = true
				}
				return msg.Stream(ctx, core.Event{
					Type:      event.Type,
					SessionID: event.SessionID,
					UserID:    msg.UserID,
					Text:      event.Content,
					Metadata:  event.Metadata,
				})
			})
			if err != nil {
				return err
			}
			if !hadTextDelta && !hadFinal && msg.Reply != nil && resp.Response != "" {
				return msg.Reply(ctx, resp.Response)
			}
			return nil
		}

		resp, err := coreClient.SendMessage(ctx, req)
		if err != nil {
			return err
		}
		if msg.Reply != nil {
			return msg.Reply(ctx, resp.Response)
		}
		return nil
	})

	channelName := os.Getenv("ORISON_CHANNEL")
	if channelName == "" {
		channelName = cfg.Channel.Default
	}

	var channel core.Channel
	switch channelName {
	case "", "local":
		channel = local.New(cfg)
	case "http", "webhook":
		channel = webhook.New(cfg.Channel.HTTPAddr)
	case "qq":
		channel = qq.New(cfg.Channel.QQ)
	default:
		fmt.Fprintf(os.Stderr, "unknown channel %q\n", channelName)
		os.Exit(1)
	}

	if err := channel.Run(ctx, handler); err != nil && !errors.Is(err, context.Canceled) {
		fmt.Fprintf(os.Stderr, "channel stopped: %v\n", err)
		os.Exit(1)
	}
}
