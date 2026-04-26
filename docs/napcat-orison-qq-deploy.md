# Agent Playbook: Install NapCat + Orison QQ Bot

> 给用户用法：把这份文档完整发给你的龙虾 / Hermes / Claude / Codex 等 Agent 助手，并告诉它：
>
> “请阅读下面的 Agent Playbook，在我的服务器上安装并验证 NapCat + Orison QQ Bot。不要泄露任何 token、QQ 号、二维码 URL 或密钥；需要我扫码时，把二维码图片发给我。”

## Goal

Install or repair an end-to-end QQ Agent stack:

- **NapCat**: QQ / OneBot v11 gateway.
- **Orison Core**: Python Agent runtime.
- **orison-channel**: Go channel that connects Orison to NapCat via OneBot v11 Forward WebSocket.
- **systemd user services**: keep NapCat, Orison Core, and the QQ channel alive.

The final result is not “process started”; the final result is verified QQ message round-trip: a trusted QQ user sends a message to the bot and receives an Orison reply.

## Safety rules for the Agent

1. Never print or send secrets back to the user unless explicitly intended as a one-time generated value.
   - Redact tokens, cookies, credentials, QQ IDs, QR login URLs, database contents, API keys, and connection strings as `[REDACTED]`.
2. Do not expose Orison Core, NapCat WebUI, or OneBot adapters to the public Internet by default.
   - Bind to `127.0.0.1` unless the user explicitly asks otherwise.
   - If Orison Core binds to a non-loopback host, require `ORISON_API_TOKEN`.
3. Do not allow arbitrary QQ users by default.
   - Configure `[channels.qq].allow_from` with explicit trusted QQ IDs.
   - Use `*` only if the user explicitly accepts the risk.
4. Do not delete an existing NapCat directory unless the user explicitly confirms.
   - It may contain QQ login state and local configs.
5. Prefer `systemctl --user` services over `nohup`, shell `&`, tmux, or Agent-managed background processes for long-running deployment.
6. Verify each layer separately before claiming success.

## Discover paths and current state

Run discovery first:

```bash
set -euo pipefail
pwd
whoami
command -v git || true
command -v go || true
command -v python3 || true
command -v /usr/bin/python3 || true
command -v systemctl || true
command -v xvfb-run || true
command -v curl || true

ps -ef | grep -Ei 'napcat|orison|qq|onebot|xvfb' | grep -v grep || true
ss -ltnp | grep -E ':(3001|6099|8765|15150)\b' || true
systemctl --user --no-pager --full status napcat.service orison-core.service orison-qq-channel.service 2>/dev/null || true
```

Find Orison:

```bash
for d in "$HOME/workspace/Orison" "$HOME/Orison" "$PWD"; do
  if [ -f "$d/config.toml" ] && [ -d "$d/orison" ]; then
    echo "ORISON_DIR=$d"
  fi
done
```

Find NapCat / QQ:

```bash
for d in "$HOME/Napcat/opt/QQ" "$HOME/napcat/opt/QQ" "/opt/QQ"; do
  if [ -x "$d/qq" ]; then
    echo "NAPCAT_QQ_DIR=$d"
  fi
done
```

If either path is missing, ask the user for the preferred install location or official source URL. Do not invent a repository URL.

## Expected variables

After discovery, set these variables for the session:

```bash
ORISON_DIR="${ORISON_DIR:-$HOME/workspace/Orison}"
NAPCAT_QQ_DIR="${NAPCAT_QQ_DIR:-$HOME/Napcat/opt/QQ}"
NAPCAT_CONFIG_DIR="$NAPCAT_QQ_DIR/resources/app/app_launcher/napcat/config"
ORISON_CORE_URL="http://127.0.0.1:8765"
ONEBOT_WS_URL="ws://127.0.0.1:3001"
```

Common ports:

- `8765`: Orison Core HTTP API.
- `3001`: NapCat OneBot v11 Forward WebSocket server.
- `15150`: NapCat OneBot HTTP server, if enabled.
- `6099`: NapCat WebUI.

## Prepare Orison

```bash
cd "$ORISON_DIR"
git status --short --branch 2>/dev/null || true
/usr/bin/python3 --version || python3 --version
go version
```

Install Python runtime dependencies:

```bash
/usr/bin/python3 -m pip install --user -r requirements.txt || python3 -m pip install --user -r requirements.txt
```

Run tests when possible:

```bash
/usr/bin/python3 -m pytest -q -rs || python3 -m pytest -q -rs || true
go test ./... || true
```

Configure safe defaults in service environment or `.env`:

```bash
ORISON_HOST=127.0.0.1
ORISON_PORT=8765
ORISON_CONFIG=config.toml
ORISON_MAX_BODY_BYTES=8388608
# Recommended if any HTTP API may be reachable beyond localhost:
ORISON_API_TOKEN=<generate-random-token>
```

Generate a random token locally if needed:

```bash
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
```

Do not paste generated tokens into chat logs. Put them into the service environment or an environment file with restrictive permissions.

Update `config.toml`:

```toml
[channels.qq]
ws_url = "ws://127.0.0.1:3001"
# If NapCat OneBot websocket has a token, put the same token here. Otherwise leave empty.
token = ""
# Replace with the user's trusted QQ ID(s), comma-separated.
allow_from = "YOUR_QQ_ID"
share_session_in_channel = true
```

If the user has not provided the trusted QQ ID, ask for it.

## Configure NapCat OneBot v11

Back up existing configs first:

```bash
mkdir -p "$NAPCAT_CONFIG_DIR"
for f in "$NAPCAT_CONFIG_DIR"/onebot11*.json; do
  [ -e "$f" ] && cp -a "$f" "$f.bak-$(date +%Y%m%d-%H%M%S)"
done
```

Configure an enabled OneBot v11 Forward WebSocket server on `127.0.0.1:3001`. Preserve unrelated existing adapters unless the user asks to remove them.

Target JSON shape:

```json
{
  "network": {
    "websocketServers": [
      {
        "enable": true,
        "name": "orison-forward-ws",
        "host": "127.0.0.1",
        "port": 3001,
        "messagePostFormat": "array",
        "reportSelfMessage": false,
        "enableForcePushEvent": true,
        "token": "",
        "debug": false,
        "heartInterval": 30000
      }
    ]
  }
}
```

If old `httpClients` repeatedly log `ECONNREFUSED` to a dead URL and Orison only uses Forward WebSocket, disable/remove those stale clients after backing up config. Restart NapCat after config changes.

## Start or verify NapCat

If NapCat is already installed:

```bash
cd "$NAPCAT_QQ_DIR"
xvfb-run -a ./qq --no-sandbox
```

In another shell, verify:

```bash
curl -fsS http://127.0.0.1:6099/ >/dev/null && echo webui-ok || true
ss -ltnp | grep -E ':(3001|6099|15150)\b' || true
```

If QQ is not logged in, use NapCat WebUI/API to generate a QR code and send the QR **image** to the user. Do not expose the QR URL or WebUI token in text.

Useful NapCat WebUI API endpoint patterns:

- `POST /api/auth/login`
- `POST /api/QQLogin/CheckLoginStatus`
- `POST /api/QQLogin/GetQQLoginQrcode`
- `POST /api/QQLogin/RefreshQRcode`
- `POST /api/OB11Config/GetConfig`

QR codes expire quickly. If expired, refresh and send a new image.

## Build Orison channel

```bash
cd "$ORISON_DIR"
mkdir -p .orison/bin
go build -o .orison/bin/orison-channel ./cmd/orison-channel
chmod 755 .orison/bin/orison-channel
```

Manual smoke:

```bash
ORISON_CHANNEL=qq ./.orison/bin/orison-channel
```

A successful connection usually creates an established TCP connection to `127.0.0.1:3001`:

```bash
ss -tnp | grep ':3001' || true
```

## Run Orison Core

Manual smoke:

```bash
cd "$ORISON_DIR"
ORISON_HOST=127.0.0.1 ORISON_PORT=8765 /usr/bin/python3 -m orison.server
```

Health check:

```bash
curl -fsS http://127.0.0.1:8765/v1/health
```

Expected result contains `"status":"ok"`.

If Core requires bearer auth, pass:

```bash
curl -fsS -H "Authorization: Bearer $ORISON_API_TOKEN" http://127.0.0.1:8765/v1/health
```

## Install user systemd services

Enable linger if unattended startup after logout/reboot is required:

```bash
loginctl show-user "$USER" -p Linger
sudo loginctl enable-linger "$USER" 2>/dev/null || true
```

Create `~/.config/systemd/user/napcat.service`:

```ini
[Unit]
Description=NapCat QQ Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/Napcat/opt/QQ
ExecStart=/usr/bin/xvfb-run -a ./qq --no-sandbox
Restart=always
RestartSec=10
KillMode=control-group
TimeoutStopSec=20

[Install]
WantedBy=default.target
```

Create `~/.config/systemd/user/orison-core.service`:

```ini
[Unit]
Description=Orison Python Core Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/workspace/Orison
Environment=PYTHONUNBUFFERED=1
Environment=ORISON_HOST=127.0.0.1
Environment=ORISON_PORT=8765
# Environment=ORISON_API_TOKEN=<same-token-if-enabled>
ExecStart=/usr/bin/python3 -m orison.server
Restart=always
RestartSec=3
KillMode=control-group
TimeoutStopSec=15

[Install]
WantedBy=default.target
```

Create `~/.config/systemd/user/orison-qq-channel.service`:

```ini
[Unit]
Description=Orison QQ Channel Service
After=network-online.target napcat.service orison-core.service
Wants=network-online.target napcat.service orison-core.service

[Service]
Type=simple
WorkingDirectory=%h/workspace/Orison
Environment=ORISON_CHANNEL=qq
# Environment=ORISON_API_TOKEN=<same-token-if-core-requires-it>
ExecStartPre=/usr/bin/bash -lc 'for i in {1..45}; do /usr/bin/curl -fsS http://127.0.0.1:8765/v1/health >/dev/null && /usr/bin/timeout 1 /usr/bin/bash -lc "</dev/tcp/127.0.0.1/3001" 2>/dev/null && exit 0; sleep 1; done; echo "Orison core or NapCat OneBot websocket not ready" >&2; exit 1'
ExecStart=%h/workspace/Orison/.orison/bin/orison-channel
Restart=always
RestartSec=3
KillMode=control-group
TimeoutStopSec=15

[Install]
WantedBy=default.target
```

Adjust paths before enabling if Orison or NapCat are elsewhere.

Enable services:

```bash
systemctl --user daemon-reload
systemctl --user enable --now napcat.service orison-core.service orison-qq-channel.service
```

## Verification checklist

Do not say “done” until these checks are complete or the exact blocker is reported.

```bash
systemctl --user is-active napcat.service orison-core.service orison-qq-channel.service
systemctl --user is-enabled napcat.service orison-core.service orison-qq-channel.service
curl -fsS http://127.0.0.1:8765/v1/health
ss -ltnp | grep -E ':(3001|6099|8765|15150)\b' || true
ss -tnp | grep ':3001' || true
journalctl --user -u napcat.service -u orison-core.service -u orison-qq-channel.service -n 120 --no-pager
```

Run tests after code/config changes:

```bash
cd "$ORISON_DIR"
/usr/bin/python3 -m pytest -q -rs || python3 -m pytest -q -rs
go test ./...
```

Live E2E test:

1. Confirm the test sender is in `[channels.qq].allow_from`.
2. Ask the user to send a private QQ message to the bot.
3. Confirm the bot replies.
4. If no reply, check channel logs, Core logs, and Orison DB counters.

SQLite smoke:

```bash
cd "$ORISON_DIR"
python3 - <<'PY'
import sqlite3
from pathlib import Path
p = Path('.orison/orison.sqlite3')
print('db_exists', p.exists(), 'size', p.stat().st_size if p.exists() else 0)
if p.exists():
    con = sqlite3.connect(p)
    for table in ['sessions', 'messages', 'provider_runs', 'provider_events']:
        try:
            print(table, con.execute(f'select count(*) from {table}').fetchone()[0])
        except Exception as e:
            print(table, 'ERR', e)
PY
```

## Troubleshooting

### NapCat WebUI works but port 3001 is missing

Likely causes:

- QQ is not logged in.
- OneBot `websocketServers` adapter is missing or disabled.
- NapCat has not restarted after config changes.

Check:

```bash
journalctl --user -u napcat.service -n 120 --no-pager
```

### Channel starts but ignores messages

Likely causes:

- Sender is not in `[channels.qq].allow_from`.
- Group chats may require explicit @mention or supported slash command.
- NapCat receiving QQ messages is not the same as Orison accepting them.

### Core is healthy but channel cannot POST

If `ORISON_API_TOKEN` is enabled in Core, the QQ channel service must have the same environment variable.

### systemd shows `status=203/EXEC`

The executable path is wrong or not executable. Check:

```bash
systemctl --user show orison-qq-channel.service -p ExecStart -p WorkingDirectory --no-pager
```

Rebuild:

```bash
cd "$ORISON_DIR"
go build -o .orison/bin/orison-channel ./cmd/orison-channel
chmod 755 .orison/bin/orison-channel
```

### QR code expired

Refresh the QR code and send a new image. Tell the user to scan quickly.

## Final report template

When finished, report:

```text
NapCat: active/inactive; QQ login verified yes/no
OneBot: 127.0.0.1:3001 listening yes/no
Orison Core: health OK yes/no
QQ channel: service active and connected yes/no
E2E: actual QQ message round trip verified yes/no
Security: Core bound to loopback; explicit QQ allowlist; no secrets printed
Remaining blocker: none / exact next user action required
```
