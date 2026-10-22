package core

import (
	"bufio"
	"fmt"
	"os"
	"strings"
)

type Config struct {
	DefaultProvider string
	DataDir         string
	Providers       map[string]ProviderConfig
	Channel         ChannelConfig
}

type ProviderConfig struct {
	Model           string
	ReasoningEffort string
	Sandbox         string
	Workdir         string
	FullAuto        bool
	ExtraArgs       []string
}

type ChannelConfig struct {
	Default  string
	CoreURL  string
	HTTPAddr string
	QQ       QQConfig
}

type QQConfig struct {
	WSURL                 string
	Token                 string
	AllowFrom             string
	ShareSessionInChannel bool
	FamilyGroupID         string
}

func LoadConfig(path string) (Config, error) {
	file, err := os.Open(path)
	if err != nil {
		return Config{}, err
	}
	defer file.Close()

	cfg := Config{
		Providers: map[string]ProviderConfig{},
		Channel: ChannelConfig{
			Default:  "local",
			CoreURL:  "http://127.0.0.1:8765",
			HTTPAddr: "127.0.0.1:8787",
			QQ: QQConfig{
				WSURL: "ws://127.0.0.1:3001",
			},
		},
	}
	section := ""
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			section = strings.TrimSuffix(strings.TrimPrefix(line, "["), "]")
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if !ok {
			return Config{}, fmt.Errorf("invalid config line: %s", line)
		}
		key = strings.TrimSpace(key)
		value = strings.TrimSpace(value)

		switch {
		case section == "provider" && key == "default":
			cfg.DefaultProvider = parseString(value)
		case section == "orison" && key == "data_dir":
			cfg.DataDir = parseString(value)
		case section == "channels":
			switch key {
			case "default":
				cfg.Channel.Default = parseString(value)
			case "core_url":
				cfg.Channel.CoreURL = strings.TrimRight(parseString(value), "/")
			case "http_addr":
				cfg.Channel.HTTPAddr = parseString(value)
			}
		case section == "channels.qq":
			switch key {
			case "ws_url":
				cfg.Channel.QQ.WSURL = parseString(value)
			case "token":
				cfg.Channel.QQ.Token = parseString(value)
			case "allow_from":
				cfg.Channel.QQ.AllowFrom = parseString(value)
			case "share_session_in_channel":
				cfg.Channel.QQ.ShareSessionInChannel = parseBool(value)
			}
		case section == "family" && key == "group_id":
			cfg.Channel.QQ.FamilyGroupID = parseString(value)
		case strings.HasPrefix(section, "providers."):
			name := strings.TrimPrefix(section, "providers.")
			provider := cfg.Providers[name]
			switch key {
			case "model":
				provider.Model = parseString(value)
			case "reasoning_effort":
				provider.ReasoningEffort = parseString(value)
			case "sandbox":
				provider.Sandbox = parseString(value)
			case "workdir":
				provider.Workdir = parseString(value)
			case "full_auto":
				provider.FullAuto = parseBool(value)
			case "extra_args":
				provider.ExtraArgs = parseStringList(value)
			}
			cfg.Providers[name] = provider
		}
	}
	if err := scanner.Err(); err != nil {
		return Config{}, err
	}
	if cfg.DefaultProvider == "" {
		return Config{}, fmt.Errorf("provider.default is required")
	}
	if _, ok := cfg.Providers[cfg.DefaultProvider]; !ok {
		return Config{}, fmt.Errorf("default provider %q is not configured", cfg.DefaultProvider)
	}
	if cfg.DataDir == "" {
		return Config{}, fmt.Errorf("orison.data_dir is required")
	}
	return cfg, nil
}

func parseString(value string) string {
	return strings.Trim(value, `"`)
}

func parseStringList(value string) []string {
	value = strings.TrimSpace(value)
	if value == "[]" {
		return nil
	}
	value = strings.TrimPrefix(strings.TrimSuffix(value, "]"), "[")
	if strings.TrimSpace(value) == "" {
		return nil
	}
	parts := strings.Split(value, ",")
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		out = append(out, parseString(strings.TrimSpace(part)))
	}
	return out
}

func parseBool(value string) bool {
	return strings.EqualFold(strings.TrimSpace(value), "true")
}
