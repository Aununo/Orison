---
name: reminders
description: Set, list, and cancel time-based reminders for QQ users; default all natural or naive times to Asia/Shanghai and use the reminder tool.
---

# Reminders

Use this skill whenever the user asks things like “提醒我…”, “某个时间叫我…”, “到点告诉我…”, “明天/今晚/半小时后提醒…”.

## Behavior

- Default timezone: `Asia/Shanghai` / 中国上海时间.
- If the user gives a relative time, resolve it against the current `Asia/Shanghai` time from system context.
- If the user gives a date/time without timezone, treat it as `Asia/Shanghai`.
- Preserve the actual reminder content succinctly, without adding extra tasks.
- In QQ group chats, reminders should notify the requester in the same group and @mention them.
- Do not implement reminders by promising to remember manually; call the `reminder` tool.

## Tool usage

To create a reminder, call:

```json
{
  "action": "add",
  "message": "要提醒用户做的事",
  "remind_at": "YYYY-MM-DD HH:MM",
  "name": "简短提醒名"
}
```

`remind_at` should be ISO-like. Examples:

- “今晚 8 点提醒我吃药” → resolve to today's or next valid Shanghai date, `20:00`.
- “明天上午 9 点半提醒我开会” → `YYYY-MM-DD 09:30` in Shanghai time.
- “半小时后提醒我看锅” → current Shanghai time plus 30 minutes.

To list reminders, call:

```json
{"action": "list"}
```

To remove a reminder, ask for/identify its `job_id`, then call:

```json
{"action": "remove", "job_id": "cron-..."}
```

## User-facing response

After creating a reminder, confirm briefly with the user using Shanghai time, e.g.:

“好的，我会在上海时间明天 09:30 在群里 @你，提醒你开会。”
