# Orison

Orison is a personal AI companion for family conversations, memory, and everyday tasks.

## Features

- QQ, terminal, and webhook channels; QQ supports merged forwards and images.
- Shared family context, bounded memory, and forgetting.
- Optional, rate-limited spontaneous replies when the family group conversation pauses.
- Daily Dream consolidation at 03:00; all schedules use `Asia/Singapore`.
- Reminders, recurring notifications, todos, web lookup, and messaging.
- Primary DeepSeek API provider with an optional `openai-codex` fallback and failure notices.

## Structure

- `orison/`: Python Core, providers, memory, tools, and scheduling.
- `channels/` and `cmd/orison-channel/`: Go transports and channel entry point.
- `skills/`: reusable companion skills.

## Run Locally

Requires Python 3.11+, Go 1.22+, private `config.toml`, and provider credentials.
Keep configuration, persona files, credentials, and runtime data out of Git.

```bash
python3 -m venv .orison/venv
.orison/venv/bin/python -m pip install -r requirements.txt
.orison/venv/bin/python -m orison.server
```

In another terminal, run `ORISON_CHANNEL=local go run ./cmd/orison-channel`.

See [DEPLOYMENT.md](DEPLOYMENT.md) for configuration, authentication, QQ setup,
validation, and upgrades.
