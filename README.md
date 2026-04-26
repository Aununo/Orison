# Orison, it's me

Orison is a personal Agent assistant. The goal of the MVP is not to recreate a heavyweight platform, but to build a simple, elegant, everyday personal Agent system that can grow over time:

- Python Core: the Agent orchestration core, including sessions, providers, memory, skills, cron, and dream.
- Providers: an extensible provider architecture; the MVP only implements `openai-codex`.
- Go Channels: the message entry layer; the MVP starts with local, HTTP webhook, and QQ (NapCat / OneBot v11).
- Memory: long-term memory, independent of provider-internal sessions.
- Skills: a reusable capability library built around `SKILL.md` as the basic unit.
- Tools: a registered tool layer for exposing reusable actions to the Agent in a controlled way.
- Loop: the runtime cycle that receives messages, recalls context, selects skills, calls tools, responds, and records useful experience.
- Dream: controlled reflection and self-learning that only distills memory and skills.

## Quick Start(Recommend very strongly)

If you want another Agent assistant (for example Openclaw, Hermes, Claude, or Codex) to install Orison with NapCat/QQ on a user's server, give it this file:

- [`docs/napcat-orison-qq-deploy.md`](docs/napcat-orison-qq-deploy.md)