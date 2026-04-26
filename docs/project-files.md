# Orison Project Files

This document records the current project layout after Phase 9 and the file
boundaries to keep clear before starting Phase 10.

## Current Phase

- Completed in `docs/plan.md`: Phase 8 Cron MVP and Phase 9 Dream MVP.
- Next phase: Phase 10 provider extension preparation.

## Source Layout

- `orison/`: Python Core package.
  - `orison/server.py`: HTTP API entry point.
  - `orison/config.py`: TOML config loading.
  - `orison/core/`: sessions, durable run records, SQLite storage.
  - `orison/providers/`: provider protocol, registry, `openai-codex` adapter.
  - `orison/memory/`: Markdown memory loading and append support.
  - `orison/skills/`: `SKILL.md` discovery and context loading.
  - `orison/tools/`: nanobot-inspired function tools split by filesystem,
    search, shell, web, runtime integration, and shared helper boundaries.
  - `orison/cron/`: persistent scheduled Core jobs.
  - `orison/dream/`: controlled reflection into memory and skills.
- `channels/`: Go channel packages.
  - `channels/core/`: channel config and shared types.
  - `channels/client/`: Python Core HTTP client.
  - `channels/local/`: terminal channel.
  - `channels/webhook/`: HTTP webhook channel.
  - `channels/qq/`: OneBot v11 / NapCat QQ channel.
- `cmd/orison-channel/`: Go channel process entry point.
- `skills/`: Orison runtime skills loaded by Python Core.
- `memory/`: editable long-term memory Markdown files.
- `tests/`: Python unit tests for Core, providers, phase structure, and skills.
- `docs/plan.md`: MVP roadmap and acceptance criteria.

## Local Runtime Files

The following files are runtime artifacts and should stay out of source review:

- `.orison/`: local Orison SQLite data directory.
- `.omx/`: local oh-my-codex runtime state and logs.
- `.pytest_cache/` and `__pycache__/`: generated Python caches.
- `*.db`, `*.db-shm`, `*.db-wal`: local SQLite/NapCat runtime databases.

The root-level `guild1.db*` files appear to be local QQ/NapCat runtime data.
They have not been moved or deleted because they may belong to an active local
runtime.

## Phase 7 Completed

- Python Core exposes `POST /v1/sessions/{session_id}/messages/stream` as SSE.
- SSE events use the normalized provider event names: `session_started`,
  `text_delta`, `status`, `tool_use`, `tool_result`, `final`, and `error`.
- `openai-codex` reads Codex JSONL incrementally and terminates the subprocess
  if the streaming request is cancelled.
- `channels/client` can parse SSE while preserving the existing request/response
  API.
- The local channel prints text deltas immediately and does not print the final
  response a second time.

## Phase 8 Completed

- Cron jobs are persisted in SQLite and support `once:`, `interval:`, and
  `daily:` schedules.
- Cron calls the same Core session flow as user messages, unless it is the
  internal `dream-daily` job.
- Cron exposes list/create/pause/resume/delete/trigger HTTP APIs.

## Phase 9 Completed

- Dream reads unprocessed provider runs using a cursor.
- Dream sends history, failures, memory, and skills to the selected provider.
- Dream only applies memory updates and `skills/*/SKILL.md` writes.
- Dream writes an audit changelog for every run.
- Core ensures a daily `dream-daily` cron job exists unless disabled by
  environment.
