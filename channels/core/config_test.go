package core

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadConfigIncludesChannelDefaultsAndQQOverrides(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.toml")
	err := os.WriteFile(path, []byte(`
[provider]
default = "openai-codex"

[providers.openai-codex]
model = "gpt-5.5"
reasoning_effort = "high"
sandbox = "workspace-write"
workdir = "."
full_auto = false
extra_args = []

[orison]
data_dir = ".orison"

[channels]
default = "qq"
core_url = "http://core.local:8765/"
http_addr = "127.0.0.1:9999"

[channels.qq]
ws_url = "ws://127.0.0.1:3001"
token = "secret"
allow_from = "100,200"
share_session_in_channel = true
`), 0o600)
	if err != nil {
		t.Fatal(err)
	}

	cfg, err := LoadConfig(path)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Channel.Default != "qq" || cfg.Channel.CoreURL != "http://core.local:8765" {
		t.Fatalf("unexpected channel config: %#v", cfg.Channel)
	}
	if cfg.Channel.QQ.Token != "secret" || !cfg.Channel.QQ.ShareSessionInChannel {
		t.Fatalf("unexpected qq config: %#v", cfg.Channel.QQ)
	}
}
