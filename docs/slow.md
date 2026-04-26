# Orison 回复速度偏慢分析记录

本文记录 2026-04-26 对当前 Orison 回复链路的只读分析结论。

## 问题摘要

当前机器人回复慢，主要慢点不在 QQ/NapCat 连接本身，而在 Orison 调用模型的链路上：

1. Go Channel 接收消息。
2. Python Core 组装上下文。
3. Python Core 通过 `openai-codex` provider 调用 `codex exec`。
4. Codex CLI 执行模型推理并返回结果。
5. Go Channel 把最终结果发回 QQ 或其他通道。

## 原因排序

### 1. 每条用户消息实际可能触发两次 Codex 调用

`SessionManager._build_system_context()` 会先调用 provider 做一次技能选择，再执行正式回答。

相关证据：

- `orison/core/session.py:311`：构造系统上下文时调用 `_select_relevant_skill_context(...)`。
- `orison/core/session.py:345`：技能选择 prompt 要求模型只返回 JSON。
- `orison/core/session.py:364`：技能选择通过 `provider.start_session(request)` 执行。
- `tests/test_skills.py:96`：测试明确断言一次用户请求会产生一次 skill selection request。

推论：

- 用户发一条消息时，正式回答前会多一次内部 LLM/CLI 调用。
- 当前 `provider_runs` 表只记录主回答，不记录这次内部技能选择调用，所以数据库中看到的运行记录低估了真实耗时。

### 2. Provider 使用 Codex CLI 子进程，不是常驻轻量 API

`openai-codex` provider 每轮通过 `codex exec` 或 `codex exec resume` 调用 Codex CLI。

相关证据：

- `orison/providers/openai_codex.py:156`：`healthcheck()` 每次检查 `codex exec --help`。
- `orison/providers/openai_codex.py:190`：非流式 `_run()` 开始时执行 healthcheck。
- `orison/providers/openai_codex.py:202`：通过 `subprocess.run(...)` 执行 `codex exec`。
- `orison/providers/openai_codex.py:88`：流式路径通过 `subprocess.Popen(...)` 执行 `codex exec`。

实测补充：

- `codex exec --help >/dev/null` 约 `0.14s`，healthcheck 本身不是主因。
- 主因更可能是后续 Codex CLI 会话启动/恢复、上下文加载和模型推理。

### 3. 当前模型配置偏重

当前配置使用高能力模型和高推理强度：

- `config.toml:5`：`model = "gpt-5.5"`
- `config.toml:6`：`reasoning_effort = "high"`

推论：

- 对普通聊天机器人场景，这个配置会优先保证复杂推理质量，但会牺牲首 token 和最终响应速度。

### 4. 短消息也带入了很大的上下文

从 `.orison/orison.sqlite3` 的 `provider_events` 中读取到的主回答 usage：

- `hi`：`input_tokens = 50441`，`cached_input_tokens = 11008`
- `请只回复 ok`：`input_tokens = 25011`，`cached_input_tokens = 5504`
- 多次 `orison ping`：约 `25020` 到 `25067` input tokens

推论：

- 慢不是由用户消息长度导致。
- Codex CLI / 会话 / 系统上下文带入了大量输入 token。
- 技能选择调用未记录在 `provider_runs`，所以真实每轮 token 成本还会更高。

### 5. QQ 通道不是流式发送，体感延迟更明显

Local 终端通道配置了 `Stream`，可以走 Core 的 SSE 流式接口；QQ 通道只配置了 `Reply`，没有配置 `Stream`。

相关证据：

- `channels/local/local.go:82`：local channel 设置了 `Stream` 回调。
- `channels/qq/qq.go:146`：QQ channel 创建 `core.Message`。
- `channels/qq/qq.go:153`：QQ channel 只设置 `Reply` 回调。
- `cmd/orison-channel/main.go:42`：只有 `msg.Stream != nil` 时才调用 `coreClient.StreamMessage(...)`。
- `cmd/orison-channel/main.go:65`：否则调用 `coreClient.SendMessage(...)`，等待完整响应。

推论：

- QQ 用户不会看到增量输出，只能等完整回答完成后收到一次消息。
- 即使模型本身有流式输出能力，QQ 侧当前也没有利用。

## 当前不是主因的线索

- `.orison/channel-qq.log` 只显示 QQ 已连接，没有看到重连或超时错误。
- `.orison/core.log` 只显示 Core 已启动，没有直接记录异常。
- `codex exec --help` 本地执行约 `0.14s`，不是主要耗时来源。
- 仓库整体体积约 `9.3M`，本身不大；但 Codex CLI 实际注入的上下文仍然很大。

## 未知项

当前日志没有记录每个阶段的 wall-clock 耗时，因此还不能精确拆分：

- 技能选择调用耗时多少。
- 主回答 Codex CLI 启动/恢复耗时多少。
- 模型首 token 时间多少。
- 模型最终完成时间多少。
- QQ 发送 API 耗时多少。

如果后续要精确定位，应增加分段耗时日志：

- Core 收到请求时间。
- 技能选择开始/结束时间。
- 主 provider 调用开始/首事件/结束时间。
- Channel 收到 Core 响应时间。
- QQ API send message 开始/结束时间。

## 结论

当前最可能的慢点组合是：

`前置技能选择 LLM 调用` + `Codex CLI 每轮执行/恢复` + `gpt-5.5 high reasoning` + `大上下文输入` + `QQ 非流式返回`

其中前四项影响真实耗时，第五项放大用户体感等待。
