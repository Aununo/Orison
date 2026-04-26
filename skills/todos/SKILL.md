---
name: todos
description: Decide when to use Orison's persistent todo tool instead of memory or reminders.
---

# Todo Routing

Use the `todo` tool for stateful, actionable tasks the user wants tracked as a checklist or backlog item.

## Use `todo.add` when

- The user says “加个待办”, “创建 todo”, “记一个任务”, “待办一下 …”.
- The request is something to do later, not a stable user preference or project fact.
- If the user provides a deadline such as “明天晚上前”, store it as `due_at`; do not schedule a reminder unless the user explicitly asks to be reminded.

## Use `todo.list` when

- The user asks “我有哪些待办”, “todo 列表”, “还剩什么任务”.
- Default to open todos for the current user/session scope.

## Use `todo.done` / `todo.update` / `todo.remove` when

- The user says a task is done, cancelled, renamed, rescheduled, or should be deleted.
- If the user refers to “第一个/第二个”, list current todos first if the id is ambiguous.

## Do not confuse with memory or reminders

- Use `memory` only for durable preferences, project facts, and behavior guidance that should affect future conversations.
- Use `reminder` only when the user explicitly asks to be reminded/notified at a time.
- A todo can have `due_at` without a reminder.

## Scope

Todos are scoped to the current channel/user/group. In QQ group chats, default to the requesting user's own todo list in that group unless the user explicitly says it is a group-level task.
