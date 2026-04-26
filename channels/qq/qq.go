// Package qq implements the Orison OneBot v11/NapCat channel.
package qq

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"orison/channels/core"
)

type Channel struct {
	Config core.QQConfig

	conn    *wsConn
	connMu  sync.Mutex
	selfID  int64
	echoSeq atomic.Int64
	pending sync.Map
	seenMu  sync.Mutex
	seen    map[string]time.Time
}

func New(config core.QQConfig) *Channel {
	if config.WSURL == "" {
		config.WSURL = "ws://127.0.0.1:3001"
	}
	return &Channel{Config: config, seen: map[string]time.Time{}}
}

func (c *Channel) Name() string { return "qq" }

func (c *Channel) Run(ctx context.Context, handler core.Handler) error {
	for {
		if err := c.connect(ctx); err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			log.Printf("qq: connect failed: %v", err)
			if !sleepContext(ctx, 3*time.Second) {
				return ctx.Err()
			}
			continue
		}
		go func() {
			<-ctx.Done()
			c.close()
		}()
		log.Printf("qq: connected to OneBot at %s", c.Config.WSURL)

		err := c.readLoop(ctx, handler)
		c.close()
		if ctx.Err() != nil {
			return ctx.Err()
		}
		log.Printf("qq: disconnected, reconnecting: %v", err)
		if !sleepContext(ctx, 3*time.Second) {
			return ctx.Err()
		}
	}
}

func (c *Channel) connect(ctx context.Context) error {
	conn, err := dialWebSocket(ctx, c.Config.WSURL, c.Config.Token)
	if err != nil {
		return err
	}
	c.connMu.Lock()
	c.conn = conn
	c.connMu.Unlock()
	return nil
}

func (c *Channel) close() {
	c.connMu.Lock()
	defer c.connMu.Unlock()
	if c.conn != nil {
		_ = c.conn.close()
		c.conn = nil
	}
}

func (c *Channel) readLoop(ctx context.Context, handler core.Handler) error {
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		var payload map[string]any
		if err := c.currentConn().readJSON(&payload); err != nil {
			return err
		}
		if echo, ok := payload["echo"].(string); ok {
			if ch, loaded := c.pending.LoadAndDelete(echo); loaded {
				ch.(chan map[string]any) <- payload
			}
			continue
		}
		if payload["post_type"] == "message" {
			go c.handleMessage(ctx, handler, payload)
		}
	}
}

func (c *Channel) handleMessage(ctx context.Context, handler core.Handler, payload map[string]any) {
	messageType, _ := payload["message_type"].(string)
	userID := int64From(payload["user_id"])
	if selfID := int64From(payload["self_id"]); selfID != 0 {
		c.selfID = selfID
	}
	groupID := int64From(payload["group_id"])
	messageID := int64From(payload["message_id"])
	if userID == 0 || userID == c.selfID || !c.allowed(userID) {
		return
	}
	if c.isDuplicate(messageID) {
		return
	}

	text, metadata := parseOneBotMessage(payload)
	if strings.TrimSpace(text) == "" {
		if count, _ := metadata["image_count"].(int); count > 0 {
			text = "[QQ image message]"
		} else {
			return
		}
	}
	shouldHandle, normalizedText := shouldHandleQQMessage(messageType, text, metadata, c.selfID)
	if !shouldHandle {
		return
	}
	text = normalizedText

	sessionID := qqSessionID(messageType, userID, groupID, c.Config.ShareSessionInChannel)
	reply := func(replyCtx context.Context, reply string) error {
		return c.sendReply(replyCtx, messageType, userID, groupID, reply, nil)
	}
	msg := core.Message{
		SessionID: sessionID,
		Channel:   "qq",
		UserID:    strconv.FormatInt(userID, 10),
		Text:      text,
		Metadata:  metadata,
		Reply:     reply,
		Stream: func(streamCtx context.Context, event core.Event) error {
			switch event.Type {
			case "tool_use":
				return reply(streamCtx, formatToolUseEcho(event))
			case "final":
				return c.sendReply(streamCtx, messageType, userID, groupID, event.Text, event.Metadata)
			case "error":
				if event.Text == "" {
					return nil
				}
				return reply(streamCtx, "出错了："+event.Text)
			}
			return nil
		},
	}
	if err := handler.HandleMessage(ctx, msg); err != nil {
		log.Printf("qq: handle message failed: %v", err)
	}
}

func (c *Channel) callAPI(ctx context.Context, action string, params map[string]any) (map[string]any, error) {
	echo := "orison-" + strconv.FormatInt(c.echoSeq.Add(1), 10)
	ch := make(chan map[string]any, 1)
	c.pending.Store(echo, ch)
	defer c.pending.Delete(echo)

	req := map[string]any{
		"action": action,
		"params": params,
		"echo":   echo,
	}
	if params == nil {
		req["params"] = map[string]any{}
	}
	if err := c.currentConn().writeJSON(req); err != nil {
		return nil, err
	}

	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case resp := <-ch:
		if status, _ := resp["status"].(string); status != "" && status != "ok" {
			return resp, fmt.Errorf("onebot api %s failed: %v", action, resp["wording"])
		}
		if data, ok := resp["data"].(map[string]any); ok {
			return data, nil
		}
		return resp, nil
	case <-time.After(15 * time.Second):
		return nil, fmt.Errorf("onebot api %s timed out", action)
	}
}

func (c *Channel) sendReply(ctx context.Context, messageType string, userID int64, groupID int64, text string, metadata map[string]any) error {
	message := buildQQReplyMessage(text, metadata)
	if message == nil {
		return nil
	}
	if messageType == "group" {
		_, err := c.callAPI(ctx, "send_group_msg", map[string]any{
			"group_id": groupID,
			"message":  message,
		})
		return err
	}
	_, err := c.callAPI(ctx, "send_private_msg", map[string]any{
		"user_id": userID,
		"message": message,
	})
	return err
}

func buildQQReplyMessage(text string, metadata map[string]any) any {
	text = formatQQReply(text)
	images := qqReplyImages(metadata)
	if strings.TrimSpace(text) == "" && len(images) == 0 {
		return nil
	}
	if len(images) == 0 {
		return text
	}
	segments := []any{}
	if strings.TrimSpace(text) != "" {
		segments = append(segments, map[string]any{"type": "text", "data": map[string]any{"text": text}})
	}
	for _, image := range images {
		segments = append(segments, map[string]any{"type": "image", "data": map[string]any{"file": image}})
	}
	return segments
}

func qqReplyImages(metadata map[string]any) []string {
	if metadata == nil {
		return nil
	}
	rawImages, ok := metadata["images"].([]any)
	if !ok {
		return nil
	}
	var images []string
	for _, raw := range rawImages {
		image, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		for _, key := range []string{"file", "url", "data"} {
			value, _ := image[key].(string)
			if value == "" {
				continue
			}
			if strings.HasPrefix(value, "data:image/") {
				if idx := strings.Index(value, ","); idx >= 0 {
					value = "base64://" + value[idx+1:]
				}
			}
			images = append(images, value)
			break
		}
	}
	return images
}

func (c *Channel) currentConn() *wsConn {
	c.connMu.Lock()
	defer c.connMu.Unlock()
	return c.conn
}

func (c *Channel) allowed(userID int64) bool {
	allow := strings.TrimSpace(c.Config.AllowFrom)
	if allow == "*" {
		return true
	}
	if allow == "" {
		return false
	}
	id := strconv.FormatInt(userID, 10)
	for _, part := range strings.Split(allow, ",") {
		if strings.TrimSpace(part) == id {
			return true
		}
	}
	return false
}

func (c *Channel) isDuplicate(messageID int64) bool {
	if messageID == 0 {
		return false
	}
	now := time.Now()
	key := strconv.FormatInt(messageID, 10)
	c.seenMu.Lock()
	defer c.seenMu.Unlock()
	if _, ok := c.seen[key]; ok {
		return true
	}
	c.seen[key] = now
	for id, seenAt := range c.seen {
		if now.Sub(seenAt) > 10*time.Minute {
			delete(c.seen, id)
		}
	}
	return false
}

func qqSessionID(messageType string, userID int64, groupID int64, shareGroup bool) string {
	if messageType == "group" {
		if shareGroup {
			return fmt.Sprintf("qq:g:%d", groupID)
		}
		return fmt.Sprintf("qq:%d:%d", groupID, userID)
	}
	return fmt.Sprintf("qq:%d", userID)
}

func formatToolUseEcho(event core.Event) string {
	text := strings.TrimSpace(event.Text)
	if text == "" {
		if name, ok := event.Metadata["tool_name"].(string); ok {
			text = strings.TrimSpace(name)
		}
	}
	if text == "" {
		text = "工具"
	}
	return "🔧 正在调用工具：" + compactForQQ(text, 120)
}

var (
	qqMarkdownLinkRE     = regexp.MustCompile(`\[([^\]]+)\]\(([^\s)]+)\)`)
	qqMarkdownHeadingRE  = regexp.MustCompile(`(?m)^\s{0,3}#{1,6}\s+`)
	qqMarkdownQuoteRE    = regexp.MustCompile(`(?m)^\s{0,3}>\s?`)
	qqMarkdownEmphasisRE = regexp.MustCompile("(\\*\\*|__|~~|`)")
)

func formatQQReply(text string) string {
	text = strings.TrimSpace(text)
	if text == "" {
		return ""
	}
	text = qqMarkdownLinkRE.ReplaceAllString(text, "$1 ($2)")
	text = qqMarkdownHeadingRE.ReplaceAllString(text, "")
	text = qqMarkdownQuoteRE.ReplaceAllString(text, "")
	text = qqMarkdownEmphasisRE.ReplaceAllString(text, "")
	return strings.TrimSpace(text)
}

func compactForQQ(text string, limit int) string {
	text = strings.TrimSpace(text)
	if len(text) <= limit {
		return text
	}
	if limit <= 3 {
		return text[:limit]
	}
	return text[:limit-3] + "..."
}

func shouldHandleQQMessage(messageType string, text string, metadata map[string]any, selfID int64) (bool, string) {
	trimmed := strings.TrimSpace(text)
	if messageType != "group" {
		return true, trimmed
	}
	if mentionedSelf(metadata, selfID) {
		return true, trimmed
	}
	if trimmed == "/ping" || strings.HasPrefix(trimmed, "/ping ") {
		return true, trimmed
	}
	return false, ""
}

func mentionedSelf(metadata map[string]any, selfID int64) bool {
	if selfID == 0 {
		return false
	}
	want := strconv.FormatInt(selfID, 10)
	switch atQQs := metadata["at_qqs"].(type) {
	case []string:
		for _, qq := range atQQs {
			if qq == want {
				return true
			}
		}
	case []any:
		for _, raw := range atQQs {
			if fmt.Sprint(raw) == want {
				return true
			}
		}
	}
	return false
}

func parseOneBotMessage(payload map[string]any) (string, map[string]any) {
	metadata := map[string]any{
		"platform":     "qq",
		"message_type": payload["message_type"],
		"message_id":   payload["message_id"],
		"group_id":     payload["group_id"],
		"raw_message":  payload["raw_message"],
	}
	var parts []string
	var atQQs []string
	var images []map[string]any
	imageCount := 0
	switch message := payload["message"].(type) {
	case string:
		parts = append(parts, message)
	case []any:
		for _, raw := range message {
			seg, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			segType, _ := seg["type"].(string)
			data, _ := seg["data"].(map[string]any)
			switch segType {
			case "text":
				if data != nil {
					if text, _ := data["text"].(string); text != "" {
						parts = append(parts, text)
					}
				}
			case "image":
				imageCount++
				if data != nil {
					image := map[string]any{}
					for _, key := range []string{"url", "file", "path"} {
						if value, _ := data[key].(string); value != "" {
							image[key] = value
						}
					}
					if len(image) > 0 {
						images = append(images, image)
					}
				}
			case "at":
				if data != nil {
					if qq := fmt.Sprint(data["qq"]); qq != "" && qq != "<nil>" {
						atQQs = append(atQQs, qq)
					}
				}
			}
		}
	}
	if len(parts) == 0 {
		if rawMessage, _ := payload["raw_message"].(string); rawMessage != "" {
			parts = append(parts, rawMessage)
		}
	}
	metadata["image_count"] = imageCount
	if len(images) > 0 {
		metadata["images"] = images
	}
	if len(atQQs) > 0 {
		metadata["at_qqs"] = atQQs
	}
	return strings.TrimSpace(strings.Join(parts, "")), metadata
}

func int64From(value any) int64 {
	switch v := value.(type) {
	case int64:
		return v
	case int:
		return int64(v)
	case float64:
		return int64(v)
	case json.Number:
		n, _ := v.Int64()
		return n
	case string:
		n, _ := strconv.ParseInt(v, 10, 64)
		return n
	default:
		return 0
	}
}

func sleepContext(ctx context.Context, duration time.Duration) bool {
	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}
