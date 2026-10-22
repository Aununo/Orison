package qq

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"testing"
	"time"

	"orison/channels/core"
)

type imageTransport func(*http.Request) (*http.Response, error)

func (f imageTransport) RoundTrip(req *http.Request) (*http.Response, error) { return f(req) }

func imageSegment(kind string, data map[string]any) map[string]any {
	return map[string]any{"type": kind, "data": data}
}

var testPNG, _ = base64.StdEncoding.DecodeString("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aKf8AAAAASUVORK5CYII=")

func TestForwardRecordsResolveNestedImagesAndPreserveAttribution(t *testing.T) {
	for _, shape := range []string{"messages", "message"} {
		t.Run(shape, func(t *testing.T) {
			var actions []string
			api := func(ctx context.Context, action string, params map[string]any) (map[string]any, error) {
				actions = append(actions, action)
				if action == "get_image" {
					if params["file"] != "nested.png" {
						t.Fatalf("unexpected image lookup: %#v", params)
					}
					return map[string]any{"base64": base64.StdEncoding.EncodeToString(testPNG)}, nil
				}
				if action != "get_forward_msg" {
					t.Fatalf("unexpected action: %s", action)
				}
				if params["id"] == "nested" {
					return map[string]any{shape: []any{imageSegment("node", map[string]any{
						"nickname": "Quoted B", "user_id": "202", "content": []any{
							imageSegment("text", map[string]any{"text": "Second message"}),
							imageSegment("image", map[string]any{"file": "nested.png"}),
							imageSegment("at", map[string]any{"qq": "999"}),
						},
					})}}, nil
				}
				return map[string]any{shape: []any{map[string]any{
					"sender": map[string]any{"nickname": "Quoted A", "user_id": "101"}, "time": int64(1768478400),
					"message": []any{imageSegment("text", map[string]any{"text": "First message"}),
						imageSegment("image", map[string]any{"url": "https://image.example.test/first.png"}),
						imageSegment("forward", map[string]any{"id": "nested"})},
				}}}, nil
			}
			client := &http.Client{Transport: imageTransport(func(req *http.Request) (*http.Response, error) {
				if req.Header.Get("Authorization") != "" {
					t.Fatal("OneBot credentials must not reach image servers")
				}
				return &http.Response{StatusCode: 200, Body: io.NopCloser(bytes.NewReader(testPNG)), Header: make(http.Header)}, nil
			})}
			text, metadata := parseIncomingMessage(context.Background(), map[string]any{
				"message_type": "group", "group_id": int64(303), "message": []any{imageSegment("forward", map[string]any{"id": "root"})},
			}, api, client)
			for _, want := range []string{"Quoted A", "Quoted B", "First message", "Second message", "[Image 1]", "[Image 2]", "+08:00"} {
				if !strings.Contains(text, want) {
					t.Fatalf("missing %q in %s", want, text)
				}
			}
			if strings.Index(text, "First message") > strings.Index(text, "Second message") {
				t.Fatal("forwarded messages were reordered")
			}
			if metadata["image_count"] != 2 || len(metadata["images"].([]map[string]any)) != 2 {
				t.Fatalf("images were lost: %#v", metadata)
			}
			for _, image := range metadata["images"].([]map[string]any) {
				if !strings.HasPrefix(image["data"].(string), "data:image/png;base64,") || image["file"] != nil {
					t.Fatal("expected inline pixels, not a file name")
				}
			}
			if handle, _ := shouldHandleQQMessage("group", text, metadata, 999); handle {
				t.Fatal("a quoted mention must not trigger a reply")
			}
			if strings.Join(actions, ",") != "get_forward_msg,get_forward_msg,get_image" {
				t.Fatalf("unexpected lookup order: %#v", actions)
			}
		})
	}
}

func TestCQForwardAndInlineNodeFormats(t *testing.T) {
	api := func(ctx context.Context, action string, params map[string]any) (map[string]any, error) {
		if action != "get_forward_msg" || params["id"] != "a,b" {
			t.Fatalf("CQ ID was not decoded: %s %#v", action, params)
		}
		return map[string]any{"message": []any{imageSegment("node", map[string]any{
			"nickname": "Quoted", "content": "hello &amp; world[CQ:at,qq=999]",
		})}}, nil
	}
	text, metadata := parseIncomingMessage(context.Background(), map[string]any{
		"message": "[CQ:at,qq=999] Read this [CQ:forward,id=a&#44;b]",
	}, api, nil)
	if !strings.Contains(text, "hello & world") || len(metadata["at_qqs"].([]string)) != 1 {
		t.Fatalf("CQ parsing failed: %s %#v", text, metadata)
	}
	inline, _ := parseOneBotMessage(map[string]any{"message": []any{imageSegment("forward", map[string]any{
		"content": []any{imageSegment("node", map[string]any{"content": []any{}, "message": []any{imageSegment("text", map[string]any{"text": "Inline content"})}})},
	})}})
	if !strings.Contains(inline, "Inline content") {
		t.Fatal("nonempty node.message must take precedence over empty node.content")
	}
}

func TestForwardFailuresCyclesAndBudgetsAreExplicit(t *testing.T) {
	calls := 0
	api := func(ctx context.Context, action string, params map[string]any) (map[string]any, error) {
		calls++
		return map[string]any{"messages": []any{imageSegment("forward", map[string]any{"id": "cycle"})}}, nil
	}
	text, _ := parseIncomingMessage(context.Background(), map[string]any{"message": []any{imageSegment("forward", map[string]any{"id": "cycle"})}}, api, nil)
	if calls != 1 || !strings.Contains(text, "Repeated") {
		t.Fatalf("forward cycle was not bounded: %d %s", calls, text)
	}
	fail := func(context.Context, string, map[string]any) (map[string]any, error) {
		return nil, fmt.Errorf("private response")
	}
	text, _ = parseIncomingMessage(context.Background(), map[string]any{"message": []any{imageSegment("forward", map[string]any{"id": "missing"})}}, fail, nil)
	if !strings.Contains(text, "unavailable") || strings.Contains(text, "private response") {
		t.Fatal("failure must be visible without exposing raw API details")
	}
	text, _ = parseOneBotMessage(map[string]any{"message": strings.Repeat("x", 20000)})
	if len(text) > 16000 || !strings.Contains(text, "truncated") {
		t.Fatal("forward text exceeded the Core message budget")
	}
	var nested any = []any{imageSegment("text", map[string]any{"text": "Too deep"})}
	for i := 0; i < 20; i++ {
		nested = []any{imageSegment("forward", map[string]any{"content": nested})}
	}
	text, _ = parseOneBotMessage(map[string]any{"message": nested})
	if !strings.Contains(text, "nesting limit") || strings.Contains(text, "Too deep") {
		t.Fatal("nested records were not bounded")
	}
}

func TestImageFilesAndUnusableLocalPaths(t *testing.T) {
	calls := 0
	api := func(ctx context.Context, action string, params map[string]any) (map[string]any, error) {
		calls++
		if action != "get_file" || params["file"] != "opaque-image-id" {
			t.Fatalf("unexpected file read: %s %#v", action, params)
		}
		return map[string]any{"base64": base64.StdEncoding.EncodeToString(testPNG)}, nil
	}
	text, metadata := parseIncomingMessage(context.Background(), map[string]any{"message": []any{
		imageSegment("file", map[string]any{"file_id": "opaque-image-id", "name": "photo.PNG"}),
		imageSegment("image", map[string]any{"file": "/etc/private.png", "url": "file:///etc/private.png"}),
	}}, api, nil)
	if calls != 1 || len(metadata["images"].([]map[string]any)) != 1 || !strings.Contains(text, "Image 2 unavailable") {
		t.Fatalf("local paths must not be read or passed as pixels: %s %#v", text, metadata)
	}
}

func TestPublicImageDownloadBoundaries(t *testing.T) {
	for _, raw := range []string{"file:///etc/private", "http://127.0.0.1/image", "http://[::1]/image", "https://user:secret@example.test/image", "http://169.254.169.254/metadata"} {
		if _, err := validImageURL(raw); err == nil {
			t.Fatalf("unsafe image URL accepted: %s", raw)
		}
	}
	for _, raw := range []string{"10.0.0.1", "100.64.0.1", "192.168.1.1", "198.18.0.1", "240.0.0.1", "::1", "fe80::1", "fd00::1"} {
		if publicImageIP(net.ParseIP(raw)) {
			t.Fatalf("nonpublic image address accepted: %s", raw)
		}
	}
	if !publicImageIP(net.ParseIP("1.1.1.1")) {
		t.Fatal("public image addresses should remain usable")
	}
	client := &http.Client{Transport: imageTransport(func(req *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, ContentLength: -1, Header: make(http.Header),
			Body: io.NopCloser(strings.NewReader(strings.Repeat("a", maxImageBytes+1)))}, nil
	})}
	if _, err := readQQImage(context.Background(), client, "https://image.example.test/oversized"); err == nil {
		t.Fatal("unbounded chunked image accepted")
	}
	redirect := publicImageClient().CheckRedirect
	request, _ := http.NewRequest("GET", "http://127.0.0.1/private", nil)
	if err := redirect(request, nil); err == nil {
		t.Fatal("redirects to private image addresses must be blocked")
	}
}

func TestIncomingImageCountAndPayloadBudgets(t *testing.T) {
	api := func(context.Context, string, map[string]any) (map[string]any, error) {
		return nil, fmt.Errorf("unavailable")
	}
	client := &http.Client{Transport: imageTransport(func(req *http.Request) (*http.Response, error) {
		body := append(append([]byte{}, testPNG...), bytes.Repeat([]byte{0}, 1500000)...)
		return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(bytes.NewReader(body))}, nil
	})}
	var images []any
	for i := 0; i < 12; i++ {
		images = append(images, imageSegment("image", map[string]any{"url": "https://image.example.test/large"}))
	}
	text, metadata := parseIncomingMessage(context.Background(), map[string]any{"message": images}, api, client)
	if len(metadata["images"].([]map[string]any)) != 2 || !strings.Contains(text, "budget") {
		t.Fatal("aggregate image budget was not enforced")
	}
}

func TestWebSocketRejectsOversizedForwardPayloadBeforeAllocation(t *testing.T) {
	client, server := net.Pipe()
	defer client.Close()
	defer server.Close()
	go func() {
		frame := []byte{0x81, 127, 0, 0, 0, 0, 0, 0, 0, 0}
		binary.BigEndian.PutUint64(frame[2:], 9*1024*1024)
		_, _ = server.Write(frame)
	}()
	connection := &wsConn{conn: client, br: bufio.NewReader(client)}
	if _, _, err := connection.readFrame(); err == nil {
		t.Fatal("oversized frame accepted")
	}
}

func TestForwardLookupEchoesDoNotBlockOrOvertakeFollowingMessages(t *testing.T) {
	clientConn, serverConn := net.Pipe()
	defer clientConn.Close()
	defer serverConn.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	channel := New(core.QQConfig{AllowFrom: "123"})
	channel.conn = &wsConn{conn: clientConn, br: bufio.NewReader(clientConn)}
	server := &wsConn{conn: serverConn, br: bufio.NewReader(serverConn)}
	serverErr := make(chan error, 1)
	go func() {
		for index, message := range []any{[]any{imageSegment("forward", map[string]any{"id": "record"})}, "Following question"} {
			if err := server.writeJSON(map[string]any{"post_type": "message", "message_type": "private", "self_id": 999,
				"user_id": 123, "message_id": index + 1, "message": message}); err != nil {
				serverErr <- err
				return
			}
		}
		for _, action := range []string{"get_forward_msg", "get_image"} {
			var request map[string]any
			if err := server.readJSON(&request); err != nil {
				serverErr <- err
				return
			}
			if request["action"] != action {
				serverErr <- fmt.Errorf("unexpected action: %v", request["action"])
				return
			}
			var data map[string]any
			if action == "get_forward_msg" {
				data = map[string]any{"messages": []any{imageSegment("node", map[string]any{
					"content": []any{imageSegment("image", map[string]any{"file": "cached.png"})},
				})}}
			} else {
				data = map[string]any{"base64": base64.StdEncoding.EncodeToString(testPNG)}
			}
			if err := server.writeJSON(map[string]any{"status": "ok", "retcode": 0, "data": data, "echo": request["echo"]}); err != nil {
				serverErr <- err
				return
			}
		}
	}()
	received := make(chan core.Message, 2)
	done := make(chan error, 1)
	go func() {
		done <- channel.readLoop(ctx, core.HandlerFunc(func(ctx context.Context, message core.Message) error {
			received <- message
			return nil
		}))
	}()
	for i := 0; i < 2; i++ {
		select {
		case message := <-received:
			if i == 0 && (!strings.Contains(message.Text, "[Image 1]") || len(message.Metadata["images"].([]map[string]any)) != 1) {
				t.Fatal("the forwarded image must arrive before its follow-up")
			}
			if i == 1 && message.Text != "Following question" {
				t.Fatal("follow-up message order was not preserved")
			}
		case err := <-serverErr:
			t.Fatal(err)
		case <-ctx.Done():
			t.Fatal("forward API lookup blocked WebSocket response handling")
		}
	}
	cancel()
	_ = clientConn.Close()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("message reader did not stop")
	}
}
