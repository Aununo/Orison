# Orison Deployment Runbook for Agents

Use this runbook when instructed to deploy or update Orison on a Linux server.
Run commands from the approved repository root as the deployment account. Discover
existing paths, services, ports, and private configuration before changing them.
Preserve working infrastructure and report any missing inputs that prevent progress.

## Deployment Contract

Deploy three components: NapCat/QQ, Python Core, and the Go QQ channel. Core owns
model calls, conversation state, memory, and scheduling. The channel receives QQ
events over a forward OneBot v11 WebSocket connection. Core sends proactive QQ
messages and reminders through a separate OneBot HTTP connection.

Required behavior:

- Primary: DeepSeek V4.1 Flash, API model ID `deepseek-flash`.
- Backup: `openai-codex`, model `gpt-6.1-sol`, reasoning `high`, service tier `fast`.
  Codex is only ever the backup, never `provider.default`. It is optional: while
  its OAuth login is unavailable, omit its provider section and `provider.fallback`.
- On primary failure, explain the failure and continue through Codex when it is
  configured. Try the primary again on the next turn; preserve context and
  completed tool results. Without a configured backup, primary failures fail the turn.
- Sessions keep the provider they last used. A session whose provider is removed
  from the configuration moves to `provider.default` on its next model turn.
- Use `Asia/Singapore` for dates and schedules. Dream runs daily at `03:00`.
- Observe allowed family members in the configured group silently; reply when
  mentioned or sent `/ping`. Allowed private conversations do not require mentions.
  When `family.spontaneous` is enabled, the companion may occasionally join a paused
  family group conversation unprompted; it never does so in private conversations.

The [OpenAI Fast mode documentation](https://developers.openai.com/api/docs/guides/fast-mode)
defines `service_tier="fast"`. DeepSeek documents the V4.1 Flash API ID in its
[API introduction](https://api-docs.deepseek.com/). Keep these requested settings;
do not replace models or enable unrelated capabilities during deployment.

## 1. Inspect the Host and Preserve State

1. Read an existing `AGENTS.md` and preserve it. Inspect `git status --short`;
   retain uncommitted work and intentional removals from Git tracking.
2. Locate the checkout, deployment account, service manager, active processes,
   private configuration, memory directory, runtime data, and OAuth storage.
3. Confirm Python 3.11+, Go 1.22+, Singapore timezone data, and outbound HTTPS
   access to the configured providers. Do not change the host timezone.
4. For an update, stop the QQ channel and Core before taking a consistent private
   backup of the configured data directory, configuration, persona files, and
   OAuth state. Keep backups outside the managed runtime storage budget.

Startup can migrate SQLite tables, merge configured family sessions, and resume
overdue scheduled jobs. Preserve job history and enabled/paused states. Do not
delete databases, clear memory, run `git clean`, or overwrite a persona to make
startup succeed. Retain the previous source revision and channel executable.

## 2. Install Dependencies and Build

Keep the virtual environment and executable in the ignored runtime directory:

```bash
umask 077
mkdir -p .orison/bin
python3 -m venv .orison/venv
.orison/venv/bin/python -m pip install -r requirements.txt
go build -o .orison/bin/orison-channel ./cmd/orison-channel
.orison/venv/bin/python -c 'import httpx, oauth_cli_kit; import orison.server'
```

Use the absolute checkout path in service definitions. Core loads `memory/` and
`skills/` relative to its working directory, even when `ORISON_CONFIG` is absolute.
Do not install a separate coding-agent runtime to run Orison.

## 3. Prepare Private Configuration

Prefer the operator's existing private `config.toml` and `memory/` files. They are
intentionally excluded from Git and may be absent from a fresh clone. If missing,
obtain their secure location or the required values before configuring family
identity. Do not invent family mappings or create a public configuration example.

Inspect values locally without dumping the entire file. Required fields are:

| Configuration field | Required setting or source |
| --- | --- |
| `provider.default` | `deepseek` |
| `provider.fallback` | `openai-codex` when the backup is configured; otherwise omit |
| `providers.deepseek.model` | `deepseek-flash` |
| `providers.openai-codex.model` | Backup only: `gpt-6.1-sol` |
| `providers.openai-codex.reasoning_effort` | Backup only: `high` |
| `providers.openai-codex.service_tier` | Backup only: `fast` |
| `providers.openai-codex.sandbox` / `workdir` | Backup only: preserve approved values; these do not grant filesystem tools |
| `orison.data_dir` | Existing private runtime directory; normally `.orison` |
| `channels.default` / `channels.core_url` | `qq` and the actual Core base URL |
| `channels.qq.ws_url` / `token` | Forward WebSocket URL and its OneBot access token |
| `channels.qq.allow_from` | Comma-separated approved sender IDs; do not use `*` for a family deployment |
| `channels.qq.share_session_in_channel` | `true` |
| `family.group_id` | Operator-provided family group ID, stored as a string |
| `family.father` / `family.mother` | Tables containing distinct string `user_id` values and English `name` values; optional `native_name` |
| `family.spontaneous` | Optional; disabled unless `enabled = true`. Also `probability` (default `0.2`), `cooldown_minutes` (`30`), `daily_max` (`3`), `quiet_hours` (`"23:00-08:00"`, Singapore time; `""` disables) and `settle_seconds` (`60`) |

Keep the sender allowlist consistent with the family member mappings. Store native
names with Unicode escapes when needed. Preserve English project instructions and
persisted memory. Use ordinary table headers and one-line values: the Go channel's
TOML reader does not implement the complete TOML specification or inline comments.

Private files must be owner-readable/writable only; private directories must be
owner-accessible only. Never include names, account IDs, group IDs, tokens, QR
codes, login callback URLs, or conversation data in committed documentation or
deployment reports. Verify private paths are ignored and untracked.

## 4. Configure Provider Authentication

Supply `DEEPSEEK_API_KEY` through Core's private process environment or secret
manager. Do not place its value in source, commands recorded in reports, or chat.
Without it, the primary cannot run.

The Codex backup needs an OAuth login. Skip the rest of this section when the
backup is not configured. Run authentication as the same OS account that runs
Core. Use the same OAuth token
path for interactive login and the service process. An explicit path inside
`.orison/` avoids dependence on another account's login state:

```bash
export OAUTH_CLI_KIT_TOKEN_PATH="$PWD/.orison/oauth/codex.json"
mkdir -p .orison/oauth
chmod 700 .orison/oauth
.orison/venv/bin/python -c 'from oauth_cli_kit import login_oauth_interactive; login_oauth_interactive(print_fn=print, prompt_fn=input, originator="orison", open_browser=False)'
```

Complete the login through the operator's local terminal/browser; keep authorization
links and callback input private. The upstream library provides
[interactive login and token refresh](https://github.com/pinhua33/oauth-cli-kit/blob/master/oauth_cli_kit/flow.py).
Its [token storage implementation](https://github.com/pinhua33/oauth-cli-kit/blob/master/oauth_cli_kit/storage.py)
supports `OAUTH_CLI_KIT_TOKEN_PATH` and can import compatible existing Codex CLI
credentials. Orison itself calls the OAuth-backed Responses endpoint directly.

Check credential availability without displaying tokens:

```bash
.orison/venv/bin/python - <<'PY'
from oauth_cli_kit import get_token
token = get_token()
assert token.access, "No usable OAuth access token"
print("Codex OAuth credentials are available")
PY
```

A token file can still show a future `expires` value after the server has rejected
it; a `401 token_expired` response means the login must be completed again. Do not
report the full deployment ready while a configured provider remains unverified,
and state explicitly when no backup is configured.

## 5. Connect NapCat and the QQ Channel

Reuse an existing compatible NapCat installation. Otherwise install from the
[official NapCat project](https://github.com/NapNeko/NapCatQQ) for the host platform
and let the operator complete QQ login. Follow the installed version's
[network configuration guide](https://napneko.github.io/config/basic).

Enable both OneBot endpoints, using message segments in `array` format:

| Endpoint | Orison consumer | Configuration |
| --- | --- | --- |
| Forward WebSocket server | Go QQ channel | `channels.qq.ws_url` and `channels.qq.token` |
| HTTP server | Python message/reminder tools | `ORISON_ONEBOT_HTTP_URL` and `ORISON_ONEBOT_ACCESS_TOKEN` |

The Go WebSocket default is `ws://127.0.0.1:3001`. Core's HTTP default is
`http://127.0.0.1:15150`; explicitly configure NapCat to match or set the environment
variable to its actual HTTP endpoint. A working WebSocket does not establish HTTP
delivery. A WebUI token is not a OneBot access token. Configure each endpoint's
matching credential in its consumer; Core does not read `channels.qq.token` for
HTTP sends. Use loopback bindings when components share a host. With containers,
use addresses reachable from each consumer's network namespace.

Verify that OneBot's read APIs `get_forward_msg`, `get_image`, and `get_file` are
available over the WebSocket connection. Forwarded records may arrive as standard
`message` nodes or NapCat `messages`; both are supported. The channel downloads
images into bounded inline payloads instead of asking a model to open a NapCat file
path. If an image has no usable public URL, NapCat must return base64 image content
through its file API; its `enableLocalFile2Url` setting controls this behavior in
the current [GetFile implementation](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/action/file/GetFile.ts).
Orison does not open NapCat-local paths, fetch private-network image URLs, or pass
OneBot credentials to image hosts.

Accept at most eight supported images per incoming message, 2 MiB each and 4 MiB
total. Forwarded text and nesting are also bounded; check the explicit omission
markers when validating large records. Core keeps recent image bytes in a scoped
16 MiB memory cache for 15 minutes, without writing image files. Restarting Core
clears the cache, so a previously forwarded image may need to be resent.

## 6. Start and Supervise Processes

Reuse the host's supervisor. Maintain one Core process per runtime database and
one QQ channel per bot account. Put new service definitions outside tracked source.
Use these settings for systemd services, or their supervisor equivalents:

| Setting | Core | QQ channel |
| --- | --- | --- |
| Working directory | Absolute checkout root | Same checkout root |
| Executable | `.orison/venv/bin/python -m orison.server` | `.orison/bin/orison-channel` |
| Environment | Private Core environment file | Private channel environment file |
| Recovery | Restart on failure | Restart on failure |
| File creation mask | `0077` | `0077` |

Resolve executable paths to absolute paths. Run under the deployment account,
with access to its OAuth storage. Use separate owner-only environment files and
pass each process only the credentials it needs. Configure service startup after
network availability, and use the existing persistence policy for user services.

| Variable | Process and purpose |
| --- | --- |
| `ORISON_CONFIG` | Both: absolute private configuration path |
| `ORISON_HOST` / `ORISON_PORT` | Core: normally `127.0.0.1` / `8765` |
| `ORISON_API_TOKEN` | Both: matching Core bearer token, required for non-loopback Core binding |
| `DEEPSEEK_API_KEY` | Core: private primary API credential |
| `OAUTH_CLI_KIT_TOKEN_PATH` | Core, backup only: same absolute path used during OAuth login |
| `ORISON_ONEBOT_HTTP_URL` / `ORISON_ONEBOT_ACCESS_TOKEN` | Core: OneBot HTTP delivery endpoint and credential |
| `ORISON_CHANNEL` | Channel: `qq`; use `local` for terminal validation |
| `ORISON_DREAM_DAILY_SCHEDULE` | Core: `daily:03:00` |

Core and the channel do not automatically load `.env` files. Load variables through
the supervisor or explicitly export them in the launching shell. Core's bind
address must match `channels.core_url`; setting `ORISON_HOST` does not update it.
If a Core token is configured, even loopback health requests require it.

Start NapCat, then Core, then the QQ channel. Foreground commands, when diagnosing
startup with the proper private environment already loaded, are:

```bash
.orison/venv/bin/python -m orison.server
```

```bash
ORISON_CHANNEL=qq .orison/bin/orison-channel
```

Core schedules Dream internally; do not add a second host cron job. Leave
`ORISON_DREAM_DAILY` enabled unless explicitly directed otherwise. Restarting Core
updates the saved Dream schedule while preserving its paused state and history.

## 7. Validate in Layers

Record each check as passed, failed, or not run. Keep provider connectivity and
actual delivery separate from configuration and mocked tests.

1. **Configuration:** Load it with `orison.config.load_config`; check the requested
   model settings and paths without printing family mappings or secrets.
2. **Process health:** Confirm the intended supervised processes, listening ports,
   working directories, and channel connection logs. Repeated reconnect messages
   mean the QQ channel is not ready, even if its process is running.
3. **Core HTTP:** Request `/v1/health` and `/v1/providers` using the configured bearer
   token. Expect `deepseek`, plus `openai-codex` when the backup is configured.
   Health only checks credential availability; `status="ok"` does not prove a
   model request or backup readiness.
4. **Local model:** In an authorized live validation, run
   `ORISON_CHANNEL=local .orison/bin/orison-channel` and send a short ordinary
   message. `/ping` is a built-in response and does not exercise a model.
5. **Provider requests:** Make a harmless request to
   `/v1/sessions/deploy-check-deepseek/messages`, using `channel="local"`, a
   synthetic `user_id`, and a short `message`. When the backup is configured,
   repeat with explicit provider `openai-codex` on
   `/v1/sessions/deploy-check-codex/messages`. Check the JSON `error` field as well
   as HTTP status. Use an isolated test runtime to exercise automatic failover;
   do not revoke production credentials or alter family sessions to simulate it.
6. **QQ:** In the approved test conversation, verify an addressed response, an
   allowed private response if applicable, and silent family-group observation.
   Messages from other groups or unknown senders must not enter family context.
   Also forward a small record containing text and a picture, then ask about the
   picture separately. Verify quoted authors, image recognition, and that mentions
   inside the quoted record do not trigger an unsolicited response.
7. **Scheduled delivery:** With an explicitly requested short test reminder, verify
   one confirmed delivery and a OneBot message ID. Inspect cron history for failure
   status. Do not replay pending outbox files automatically: uncertain sends may
   already have reached QQ. Remove only the temporary jobs created for this check.
8. **Dream:** Inspect `/v1/cron/jobs` for `dream-daily`, `daily:03:00`, the intended
   enabled state, and a next-run timestamp equivalent to 03:00 Singapore time.
   Stored timestamps are UTC. Do not trigger consolidation merely as a health test.

When the private Python test suite is present, run it with live tests disabled:

```bash
ORISON_RUN_LIVE_CODEX=0 .orison/venv/bin/python -m unittest discover -s tests
go test ./...
```

`tests/` is intentionally untracked and may not exist in another checkout. Report
that limitation rather than claiming a Python test pass. These tests supplement
live acceptance checks; they do not replace them.

## 8. Recovery and Handoff

| Symptom | Action |
| --- | --- |
| Missing private configuration or persona | Restore the approved private files; do not generate a replacement identity |
| Primary fails | Check `DEEPSEEK_API_KEY`, API reachability, account access, and sanitized errors |
| Codex OAuth missing or expired | Check account/token-path consistency, complete private login again, and recheck the backup; or remove the backup from the configuration |
| Scheduled jobs fail with an error from an unexpected provider | Check the target session's stored provider; it does not follow later `provider.default` changes |
| QQ replies work but reminders fail | Check the separate OneBot HTTP URL/token and cron error history |
| QQ channel reconnects repeatedly | Check forward WebSocket server mode, URL, token, QQ login, and network reachability |
| Memory/storage limit reached | Preserve pending conversations; inspect the configured data directory and maintenance results |

The managed conversation/runtime budget is 128 MiB, with at most 3,000 structured
memories. Dependencies, supervisor logs, NapCat state, and backups need their own
retention policy. Rotate logs without deleting pending message outboxes or private
memory. Keep runtime and authentication backups private.

For rollback, stop the new Core and QQ channel, restore the previous executable
and source, and restore the matching pre-migration data/configuration backup.
Restart in dependency order and repeat health checks. Do not run old code against
a migrated database without verifying compatibility.

Finish with a concise report: deployed revision/build, service names and state,
non-secret paths and endpoints, requested model settings, timezone/Dream schedule,
checks actually performed, remaining blockers, and exact stop/restart commands.
State explicitly when model access, failover, reminders, or real QQ delivery was
not tested. Do not commit, push, publish private files, or claim successful
deployment solely because this document was followed or processes started.
