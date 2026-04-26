package qq

import (
	"bufio"
	"context"
	"fmt"
	"net"
	"net/http"
	"testing"
	"time"

	"orison/channels/core"
)

func TestQQSessionID(t *testing.T) {
	tests := []struct {
		name        string
		messageType string
		userID      int64
		groupID     int64
		share       bool
		want        string
	}{
		{"private", "private", 42, 0, false, "qq:42"},
		{"group isolated", "group", 42, 88, false, "qq:88:42"},
		{"group shared", "group", 42, 88, true, "qq:g:88"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := qqSessionID(tt.messageType, tt.userID, tt.groupID, tt.share)
			if got != tt.want {
				t.Fatalf("got %q want %q", got, tt.want)
			}
		})
	}
}

func TestParseOneBotMessageSegments(t *testing.T) {
	text, metadata := parseOneBotMessage(map[string]any{
		"message_type": "group",
		"group_id":     float64(88),
		"message_id":   float64(99),
		"message": []any{
			map[string]any{"type": "text", "data": map[string]any{"text": "hello "}},
			map[string]any{"type": "image", "data": map[string]any{"url": "https://example.test/a.png"}},
			map[string]any{"type": "record", "data": map[string]any{"url": "https://example.test/a.mp3", "file": "voice.amr"}},
			map[string]any{"type": "text", "data": map[string]any{"text": "world"}},
		},
	})
	if text != "hello world" {
		t.Fatalf("unexpected text: %q", text)
	}
	if metadata["image_count"] != 1 {
		t.Fatalf("unexpected metadata: %#v", metadata)
	}
	if _, ok := metadata["audios"]; ok {
		t.Fatalf("audio modality should be removed, got audios metadata: %#v", metadata["audios"])
	}
	if _, ok := metadata["audio_count"]; ok {
		t.Fatalf("audio_count should not be recorded after audio modality removal: %#v", metadata)
	}
	images, ok := metadata["images"].([]map[string]any)
	if !ok || len(images) != 1 || images[0]["url"] != "https://example.test/a.png" {
		t.Fatalf("expected image URL metadata, got %#v", metadata["images"])
	}
}

func TestQQReplyBuildsImageSegmentsFromFinalEventMetadata(t *testing.T) {
	segments := buildQQReplyMessage("生成好了", map[string]any{
		"images": []any{map[string]any{"url": "https://example.test/sky.png"}},
	})
	parts, ok := segments.([]any)
	if !ok || len(parts) != 2 {
		t.Fatalf("expected two message segments, got %#v", segments)
	}
	textSeg := parts[0].(map[string]any)
	imageSeg := parts[1].(map[string]any)
	if textSeg["type"] != "text" || imageSeg["type"] != "image" {
		t.Fatalf("unexpected segments: %#v", parts)
	}
	imageData := imageSeg["data"].(map[string]any)
	if imageData["file"] != "https://example.test/sky.png" {
		t.Fatalf("unexpected image segment data: %#v", imageData)
	}
}

func TestQQReplyIgnoresAudioMetadataAfterAudioModalityRemoved(t *testing.T) {
	message := buildQQReplyMessage("语音功能已关闭", map[string]any{
		"audios": []any{map[string]any{
			"file": ".orison/audio/generated.mp3",
			"data": "data:audio/mpeg;base64,UklGRg==",
		}},
	})
	if message != "语音功能已关闭" {
		t.Fatalf("audio metadata should not create record segments after removal, got %#v", message)
	}
}

func TestAllowFromAndDedup(t *testing.T) {
	closed := New(core.QQConfig{})
	if closed.allowed(100) {
		t.Fatalf("empty allow_from should deny by default")
	}
	open := New(core.QQConfig{AllowFrom: "*"})
	if !open.allowed(100) {
		t.Fatalf("allow_from=* should allow all users")
	}
	channel := New(core.QQConfig{AllowFrom: "100, 200"})
	if !channel.allowed(100) || channel.allowed(300) {
		t.Fatalf("allow_from not enforced")
	}
	if channel.isDuplicate(123) {
		t.Fatal("first message should not be duplicate")
	}
	if !channel.isDuplicate(123) {
		t.Fatal("second message should be duplicate")
	}
}

func TestQQGroupTriggerRules(t *testing.T) {
	tests := []struct {
		name        string
		messageType string
		text        string
		metadata    map[string]any
		wantHandle  bool
		wantText    string
	}{
		{"private messages still pass through", "private", "hello", map[string]any{}, true, "hello"},
		{"group mention passes through", "group", "hello", map[string]any{"at_qqs": []string{"999"}}, true, "hello"},
		{"group at all is ignored", "group", "hello", map[string]any{"at_qqs": []string{"all"}}, false, ""},
		{"group ping command passes through", "group", "/ping", map[string]any{}, true, "/ping"},
		{"group ping command allows arguments", "group", "/ping now", map[string]any{}, true, "/ping now"},
		{"group ordinary text is ignored", "group", "hello", map[string]any{}, false, ""},
		{"group bilibili BV text is ignored", "group", "看看 BV1Q541167Qg", map[string]any{}, false, ""},
		{"group unknown slash command is ignored", "group", "/ovo/", map[string]any{}, false, ""},
		{"group ping prefix without command boundary is ignored", "group", "/pingpong", map[string]any{}, false, ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			gotHandle, gotText := shouldHandleQQMessage(tt.messageType, tt.text, tt.metadata, 999)
			if gotHandle != tt.wantHandle || gotText != tt.wantText {
				t.Fatalf("got (%v, %q), want (%v, %q)", gotHandle, gotText, tt.wantHandle, tt.wantText)
			}
		})
	}
}

func TestFormatToolUseEcho(t *testing.T) {
	echo := formatToolUseEcho(core.Event{Type: "tool_use", Text: "web_search"})
	if echo != "🔧 正在调用工具：web_search" {
		t.Fatalf("unexpected echo: %q", echo)
	}

	echo = formatToolUseEcho(core.Event{Type: "tool_use", Metadata: map[string]any{"tool_name": "web_fetch"}})
	if echo != "🔧 正在调用工具：web_fetch" {
		t.Fatalf("unexpected metadata echo: %q", echo)
	}
}

func TestFormatQQReplyRemovesCommonMarkdown(t *testing.T) {
	reply := formatQQReply("成都当前天气：多云间晴，约 **26℃**，体感 **27℃**。\n- 湿度 **48%**\n[详情](https://example.test)")
	want := "成都当前天气：多云间晴，约 26℃，体感 27℃。\n- 湿度 48%\n详情 (https://example.test)"
	if reply != want {
		t.Fatalf("got %q want %q", reply, want)
	}
}

func TestParseOneBotMessageRecordsAtMentions(t *testing.T) {
	_, metadata := parseOneBotMessage(map[string]any{
		"message_type": "group",
		"message": []any{
			map[string]any{"type": "at", "data": map[string]any{"qq": "999"}},
			map[string]any{"type": "text", "data": map[string]any{"text": " hello"}},
		},
	})
	atQQs, ok := metadata["at_qqs"].([]string)
	if !ok || len(atQQs) != 1 || atQQs[0] != "999" {
		t.Fatalf("expected at_qqs metadata, got %#v", metadata["at_qqs"])
	}
}

func TestQQChannelPrivateMessageRoundTripWithOneBotWebSocket(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()

	done := make(chan map[string]any, 1)
	serverErr := make(chan error, 1)
	go func() {
		conn, err := listener.Accept()
		if err != nil {
			serverErr <- err
			return
		}
		defer conn.Close()
		br := bufio.NewReader(conn)
		req, err := http.ReadRequest(br)
		if err != nil {
			serverErr <- err
			return
		}
		accept := websocketAccept(req.Header.Get("Sec-WebSocket-Key"))
		_, _ = fmt.Fprintf(conn, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n", accept)
		ws := &wsConn{conn: conn, br: br}

		_ = ws.writeJSON(map[string]any{
			"post_type":    "message",
			"message_type": "private",
			"self_id":      float64(999),
			"user_id":      float64(123),
			"message_id":   float64(456),
			"message":      "hello",
		})
		var sendReq map[string]any
		if err := ws.readJSON(&sendReq); err != nil {
			serverErr <- err
			return
		}
		done <- sendReq
		_ = ws.writeJSON(map[string]any{
			"status": "ok",
			"echo":   sendReq["echo"],
			"data":   map[string]any{"message_id": float64(789)},
		})
	}()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	channel := New(core.QQConfig{WSURL: "ws://" + listener.Addr().String(), AllowFrom: "123"})
	handled := make(chan core.Message, 1)
	errCh := make(chan error, 1)
	go func() {
		errCh <- channel.Run(ctx, core.HandlerFunc(func(ctx context.Context, msg core.Message) error {
			handled <- msg
			return msg.Reply(ctx, "reply **26℃**")
		}))
	}()

	select {
	case msg := <-handled:
		if msg.SessionID != "qq:123" || msg.Channel != "qq" || msg.Text != "hello" {
			t.Fatalf("unexpected handled message: %#v", msg)
		}
	case err := <-serverErr:
		t.Fatalf("server error: %v", err)
	case <-time.After(3 * time.Second):
		t.Fatal("timed out waiting for handled message")
	}

	select {
	case sendReq := <-done:
		if sendReq["action"] != "send_private_msg" {
			t.Fatalf("unexpected action: %#v", sendReq)
		}
		params := sendReq["params"].(map[string]any)
		if int64From(params["user_id"]) != 123 || params["message"] != "reply 26℃" {
			t.Fatalf("unexpected params: %#v", params)
		}
	case err := <-serverErr:
		t.Fatalf("server error: %v", err)
	case <-time.After(3 * time.Second):
		t.Fatal("timed out waiting for send_private_msg")
	}
	cancel()
	select {
	case <-errCh:
	case <-time.After(3 * time.Second):
		t.Fatal("channel did not stop")
	}
}
