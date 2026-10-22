---
name: todos
description: Decide when to use Orison's persistent todo tool instead of memory or reminders.
---

# Todo Routing

Use the `todo` tool for stateful, actionable tasks the user wants tracked as a checklist or backlog item.

## Use `todo.add` when

- The user asks to add a todo, create a task, or track an action item.
- The request is something to do later, not a stable user preference or project fact.
- Resolve deadlines such as "by tomorrow evening" in `Asia/Singapore` using the current time from system context. Store them as `due_at`; schedule a reminder only when explicitly requested.

## Use `todo.list` when

- The user asks for their todo list or remaining tasks.
- Default to open todos for the current user/session scope.

## Use `todo.done` / `todo.update` / `todo.remove` when

- The user says a task is done, cancelled, renamed, rescheduled, or should be deleted.
- If the user refers to the first or second item, list current todos first if the id is ambiguous.

## Do not confuse with memory or reminders

- Use `memory` only for durable preferences, project facts, and behavior guidance that should affect future conversations.
- Use `reminder` only when the user explicitly asks to be reminded/notified at a time.
- A todo can have `due_at` without a reminder.

## Scope

Todos are scoped to the current channel/user/group. In QQ group chats, default to the requesting user's own todo list in that group unless the user explicitly says it is a group-level task.
