---
name: reminders
description: Set, list, and cancel QQ reminders in Asia/Singapore; use reminder for one-time requests and cron for recurring requests.
---

# Reminders

Use this skill when the user asks to be reminded or notified at a specific time.

## Behavior

- Default timezone: `Asia/Singapore` (UTC+08:00, no daylight saving time).
- Resolve relative times against the current Singapore time in system context.
- Treat dates without a timezone as Singapore time; honor explicit offsets.
- Preserve the requested reminder content without adding extra tasks.
- In QQ group chats, notify the requester in the same group and @mention them.
- Create one-time reminders with `reminder` and recurring reminders with `cron`.
- A conversational promise does not create a reminder; confirm creation only after the tool succeeds.

## Tool Usage

Create a one-time reminder with `reminder`:

```json
{"action": "add", "message": "Attend the meeting", "remind_at": "YYYY-MM-DD 09:30", "name": "Meeting"}
```

- "Tonight at 8": choose the next applicable Singapore date at `20:00`.
- "Tomorrow at 9:30": use tomorrow's Singapore date at `09:30`.
- "In half an hour": add 30 minutes to the supplied current time.

For "every day at 9", call `cron`:

```json
{"action": "add", "name": "Daily meal", "cron_expr": "0 9 * * *", "message": "Use the message tool to remind this conversation to eat."}
```

Include the requesting person's QQ ID from context in the scheduled instruction
when a personal reminder is requested; tell `message` to use its `mentions` argument.
Use `every_seconds` for interval schedules. Proactive QQ delivery uses OneBot HTTP.
Confirm a send only after `message` returns a confirmed message ID. A queued or failed
result is not proof of delivery; do not automatically retry an unconfirmed send.

List one-time reminders with `reminder` and recurring jobs with `cron`, using
`{"action": "list"}`. Remove the identified job through the corresponding tool
with `{"action": "remove", "job_id": "cron-..."}`.

## Confirmation

Confirm the scheduled Singapore date and time, reminder content, and QQ target
briefly in the user's conversational language.
