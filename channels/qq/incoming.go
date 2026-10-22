package qq

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"path/filepath"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"
)

const (
	maxIncomingText       = 14000
	maxIncomingImages     = 8
	maxImageBytes         = 2 * 1024 * 1024
	maxIncomingImageBytes = 4 * 1024 * 1024
)

type oneBotReader func(context.Context, string, map[string]any) (map[string]any, error)

type incomingMessage struct {
	ctx                                            context.Context
	api                                            oneBotReader
	client                                         *http.Client
	text                                           strings.Builder
	images                                         []map[string]any
	mentions                                       []string
	seen                                           map[string]bool
	nodes, calls, imageCount, imageBytes, segments int
	truncated                                      bool
}

func parseIncomingMessage(ctx context.Context, payload map[string]any, api oneBotReader, client *http.Client) (string, map[string]any) {
	if client == nil && api != nil {
		client = publicImageClient()
		defer client.CloseIdleConnections()
	}
	p := incomingMessage{ctx: ctx, api: api, client: client, seen: map[string]bool{}}
	message := payload["message"]
	if message == nil {
		message = payload["raw_message"]
	}
	p.walk(message, 0)
	if api != nil && ctx.Err() != nil {
		p.append("\n[Content reading stopped; remaining attachments were not read.]")
	}
	metadata := map[string]any{"platform": "qq", "message_type": payload["message_type"],
		"message_id": payload["message_id"], "group_id": payload["group_id"], "image_count": p.imageCount}
	if len(p.images) > 0 {
		metadata["images"] = p.images
	}
	if len(p.mentions) > 0 {
		metadata["at_qqs"] = p.mentions
	}
	return strings.TrimSpace(p.text.String()), metadata
}

func (p *incomingMessage) append(text string) {
	if p.truncated {
		return
	}
	remaining := maxIncomingText - p.text.Len()
	if len(text) > remaining {
		text = text[:remaining]
		for !utf8.ValidString(text) {
			text = text[:len(text)-1]
		}
		p.truncated = true
	}
	p.text.WriteString(text)
	if p.truncated {
		p.text.WriteString("\n[Forwarded content truncated; omitted content was not read.]")
	}
}

var cqSegment = regexp.MustCompile(`\[CQ:([a-zA-Z0-9_]+)((?:,[^\]]*)?)\]`)

func cqUnescape(text string) string {
	return strings.NewReplacer("&#91;", "[", "&#93;", "]", "&#44;", ",", "&amp;", "&").Replace(text)
}

func (p *incomingMessage) walk(message any, depth int) {
	if p.truncated || p.ctx.Err() != nil {
		return
	}
	if depth > 8 {
		p.append("\n[Forward nesting limit reached.]\n")
		return
	}
	switch value := message.(type) {
	case string:
		if len(value) > maxIncomingText*2 {
			value = value[:maxIncomingText*2]
		}
		position := 0
		for _, match := range cqSegment.FindAllStringSubmatchIndex(value, 256) {
			p.append(cqUnescape(value[position:match[0]]))
			data := map[string]any{}
			for _, field := range strings.Split(strings.TrimPrefix(value[match[4]:match[5]], ","), ",") {
				if key, content, ok := strings.Cut(field, "="); ok {
					data[key] = cqUnescape(content)
				}
			}
			p.walk(map[string]any{"type": value[match[2]:match[3]], "data": data}, depth)
			position = match[1]
		}
		p.append(cqUnescape(value[position:]))
	case []any:
		for _, segment := range value {
			if p.truncated || p.segments >= 512 {
				p.append("\n[Message segment limit reached.]\n")
				break
			}
			p.walk(segment, depth)
		}
	case map[string]any:
		p.segments++
		kind, _ := value["type"].(string)
		data, _ := value["data"].(map[string]any)
		switch kind {
		case "text":
			p.append(stringValue(data["text"]))
		case "at":
			if who := stringValue(data["qq"]); who != "" {
				if depth == 0 {
					p.mentions = append(p.mentions, who)
				} else {
					p.append("[Quoted mention: " + who + "]")
				}
			}
		case "image":
			p.image(data, depth, "get_image")
		case "file":
			name := stringValue(data["name"])
			if name == "" {
				name = stringValue(data["file_name"])
			}
			switch strings.ToLower(filepath.Ext(name)) {
			case ".png", ".jpg", ".jpeg", ".gif", ".webp":
				p.image(data, depth, "get_file")
			default:
				p.append("[File attachment; content not read]")
			}
		case "forward":
			p.append("\n[Begin quoted forwarded conversation]\n")
			if content := firstContent(data); content != nil {
				p.walk(content, depth+1)
			} else if id := stringValue(data["id"]); id != "" && !p.seen[id] {
				p.seen[id] = true
				result, err := p.read("get_forward_msg", map[string]any{"id": id})
				if err != nil || firstContent(result) == nil {
					p.append("[Forwarded record unavailable; content was not read.]")
				} else {
					p.walk(firstContent(result), depth+1)
				}
			} else {
				p.append("[Repeated or unavailable forwarded record omitted.]")
			}
			p.append("\n[End quoted forwarded conversation]\n")
		case "node", "":
			p.nodes++
			if p.nodes > 96 {
				p.append("\n[Forward node limit reached.]\n")
				return
			}
			if kind == "node" {
				value = data
			}
			sender, _ := value["sender"].(map[string]any)
			author := map[string]any{}
			for _, key := range []string{"nickname", "user_id"} {
				who := stringValue(value[key])
				if who == "" {
					who = stringValue(sender[key])
				}
				if len(who) > 160 {
					who = who[:160]
				}
				if who != "" {
					author[key] = who
				}
			}
			if seconds := int64From(value["time"]); seconds > 0 {
				author["time"] = time.Unix(seconds, 0).In(time.FixedZone("Asia/Singapore", 8*3600)).Format(time.RFC3339)
			}
			encoded, _ := json.Marshal(author)
			p.append("\n[Quoted sender " + string(encoded) + "]\n")
			p.walk(firstContent(value), depth+1)
			p.append("\n")
		}
	}
}

func firstContent(data map[string]any) any {
	for _, key := range []string{"content", "message", "messages"} {
		if value, ok := data[key]; ok {
			if array, ok := value.([]any); ok && len(array) == 0 {
				continue
			}
			if value != nil && value != "" {
				return value
			}
		}
	}
	return nil
}

func stringValue(value any) string {
	if value == nil {
		return ""
	}
	return fmt.Sprint(value)
}

func (p *incomingMessage) read(action string, params map[string]any) (map[string]any, error) {
	if p.api == nil || p.calls >= 8 || p.ctx.Err() != nil {
		return nil, fmt.Errorf("OneBot content lookup unavailable or limited")
	}
	p.calls++
	return p.api(p.ctx, action, params)
}

var imageFileID = regexp.MustCompile(`^[a-zA-Z0-9_.-]{1,256}$`)

func (p *incomingMessage) image(data map[string]any, depth int, action string) {
	p.imageCount++
	index := p.imageCount
	if index > maxIncomingImages || p.imageBytes >= maxIncomingImageBytes {
		p.append(fmt.Sprintf("[Image %d omitted: image budget reached.]", index))
		return
	}
	if p.api == nil {
		image := map[string]any{"index": index}
		for _, key := range []string{"url", "file", "path", "data"} {
			if text := stringValue(data[key]); text != "" {
				image[key] = text
			}
		}
		p.images = append(p.images, image)
		if depth > 0 {
			p.append(fmt.Sprintf("[Image %d]", index))
		}
		return
	}
	rawURL := stringValue(data["url"])
	image, err := readQQImage(p.ctx, p.client, rawURL)
	if err == nil && !supportedImage(http.DetectContentType(image)) {
		err = fmt.Errorf("Image response did not contain supported pixels")
	}
	if err != nil {
		file := stringValue(data["file_id"])
		if file == "" {
			file = stringValue(data["file"])
		}
		if imageFileID.MatchString(file) && !strings.Contains(file, "..") {
			if result, lookupErr := p.read(action, map[string]any{"file": file}); lookupErr == nil {
				if encoded, ok := result["base64"].(string); ok && encoded != "" && len(encoded) <= base64.StdEncoding.EncodedLen(maxImageBytes) {
					image, err = base64.StdEncoding.DecodeString(encoded)
				} else {
					image, err = readQQImage(p.ctx, p.client, stringValue(result["url"]))
				}
			}
		}
	}
	mime := http.DetectContentType(image)
	if err != nil || len(image) == 0 || len(image) > maxImageBytes || p.imageBytes+len(image) > maxIncomingImageBytes || !supportedImage(mime) {
		p.append(fmt.Sprintf("[Image %d unavailable: download failed, unsupported format, or size limit; contents were not read.]", index))
		return
	}
	p.imageBytes += len(image)
	p.images = append(p.images, map[string]any{"index": index, "data": "data:" + mime + ";base64," + base64.StdEncoding.EncodeToString(image)})
	p.append(fmt.Sprintf("[Image %d]", index))
}

func supportedImage(mime string) bool {
	return mime == "image/png" || mime == "image/jpeg" || mime == "image/gif" || mime == "image/webp"
}

func validImageURL(raw string) (*url.URL, error) {
	u, err := url.Parse(raw)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Hostname() == "" || u.User != nil || u.Fragment != "" {
		return nil, fmt.Errorf("Image URL must be public HTTP(S)")
	}
	if ip := net.ParseIP(u.Hostname()); ip != nil && !publicImageIP(ip) {
		return nil, fmt.Errorf("Private image address blocked")
	}
	return u, nil
}

func publicImageIP(ip net.IP) bool {
	if !ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() {
		return false
	}
	for _, block := range []string{"0.0.0.0/8", "100.64.0.0/10", "192.0.0.0/24", "192.0.2.0/24", "198.18.0.0/15",
		"198.51.100.0/24", "203.0.113.0/24", "240.0.0.0/4", "2001:db8::/32", "64:ff9b::/96", "64:ff9b:1::/48"} {
		_, subnet, _ := net.ParseCIDR(block)
		if subnet.Contains(ip) {
			return false
		}
	}
	return true
}

func publicImageClient() *http.Client {
	return &http.Client{
		Timeout: 10 * time.Second,
		Transport: &http.Transport{DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
			host, port, err := net.SplitHostPort(address)
			if err != nil {
				return nil, err
			}
			addresses, err := net.DefaultResolver.LookupIPAddr(ctx, host)
			if err != nil || len(addresses) == 0 {
				return nil, fmt.Errorf("Image host resolution failed")
			}
			for _, address := range addresses {
				if !publicImageIP(address.IP) {
					return nil, fmt.Errorf("Private image address blocked")
				}
			}
			dialer := net.Dialer{Timeout: 5 * time.Second}
			return dialer.DialContext(ctx, network, net.JoinHostPort(addresses[0].IP.String(), port))
		}},
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= 3 {
				return fmt.Errorf("Image redirect limit reached")
			}
			_, err := validImageURL(req.URL.String())
			return err
		},
	}
}

func readQQImage(ctx context.Context, client *http.Client, raw string) ([]byte, error) {
	if _, err := validImageURL(raw); err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, raw, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "Orison QQ image reader")
	response, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("Image request failed")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK || response.ContentLength > maxImageBytes {
		return nil, fmt.Errorf("Image download failed or too large")
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, maxImageBytes+1))
	if err != nil || len(body) > maxImageBytes {
		return nil, fmt.Errorf("Image download failed or too large")
	}
	return body, nil
}
