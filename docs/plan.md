# Orison MVP Plan

## 0. 总体目标

Orison 是一款自用 Agent 助手。MVP 的目标不是复制一个重型平台，而是做一个简洁、优美、可日常使用、后续能扩展的个人 Agent 系统。

最高原则：

- Orison 首先是一个具备自我思考能力的智能体系统，不是关键词匹配器、打分器或规则路由器。
- 凡是涉及理解用户意图、选择 memory、选择 skills、决定下一步行动、反思经验沉淀的能力，默认应由 Agent/LLM 基于语义和上下文自主判断。
- 不要把英文关键词、硬编码词表、简单字符串匹配、分数加权当成主要智能路径；用户可能使用中文或其他语言，这类规则天然脆弱。
- 规则代码只应用于确定性的工程边界，例如 HTTP 路由、配置校验、文件格式校验、权限边界和错误归一化；不能用它替代 Agent 对任务含义的判断。
- 如果必须在 provider/LLM 不可用时提供 fallback，fallback 必须保守、可解释、可关闭，且不能反过来定义系统的主要行为。

核心闭环：

- Python Core：Agent 编排核心，包括 session、provider、memory、skills、cron、dream。
- Providers：保留可扩展 provider 架构，MVP 只实现 `openai-codex`。
- Go Channels：消息入口层，MVP 先做 local、HTTP webhook 和 QQ（NapCat / OneBot v11）。
- Memory：长期记忆，独立于 provider 内部会话。
- Skills：可复用能力库，以 `SKILL.md` 为基本单位。
- Dream：可控反思和自学习，只沉淀 memory 和 skills。

## 1. 参考项目与职责边界

Orison 明确参考相邻三个项目，但三者的职责不同，不能混在一起照搬。

### 1.1 Core 借鉴 `../nanobot`

Python Core 的核心架构主要借鉴 `../nanobot`。

借鉴点：

- 轻量、可读、可长期运行的 Agent core。
- provider registry 和 provider 抽象边界。
- session 管理、上下文组装、message history 的模块化方式。
- memory、skills、cron、dream 等能力作为 Core 内部模块，而不是散落在入口层。
- 配置、启动、测试和文档都服务于“个人 Agent 可日常运行”。

不照搬：

- 不在 MVP 复制 nanobot 的完整多平台 channels 生态。
- 不引入大而全的 MCP、多 provider、多媒体、多 deployment 路径。
- 不把 Orison 做成通用平台，先保持自用闭环。

### 1.2 Channels 借鉴 `../cc-connect`

聊天入口和外部消息平台边界主要借鉴 `../cc-connect`，MVP 的外部聊天平台优先借鉴 `../cc-connect/platform/qq` 做 QQ 通道。

借鉴点：

- 平台 adapter 和 agent/core 解耦。
- Go 侧负责消息接入、session key、平台元数据、输出投递。
- channel 不知道 provider 细节，只调用 Core API。
- QQ 通道借鉴 `cc-connect` 的 OneBot v11 Forward WebSocket 适配，默认面向 NapCat，也保留兼容其他 OneBot v11 实现的边界。
- 后续增加 Feishu、Telegram、Slack、WeChat 等平台时，只新增 channel adapter。
- 错误、断连、平台能力差异在 channel 层处理，不污染 Python Core。

不照搬：

- MVP 不实现 cc-connect 的多 Agent 插件系统。
- MVP 不做完整 Web 管理 UI。
- MVP 不让 Go 侧直接驱动 Codex、Claude、Gemini 等 Agent CLI。
- MVP 不实现 QQ 官方机器人平台 `qqbot`；只做 NapCat / OneBot v11 QQ 通道。

### 1.3 Dream / 自进化借鉴 `../hermes-agent`

自进化、反思学习和长期成长能力主要借鉴 `../hermes-agent`。

借鉴点：

- memory manager 思路：长期记忆作为独立 provider/context block 注入，而不是简单拼接历史。
- 从经验中沉淀 skills。
- 反思 loop 读取历史、失败案例、当前 memory 和 skills，再产出可审计更新。
- 跨 session 的用户模型和长期偏好。
- cron 或手动触发的学习循环。

安全边界：

- Dream MVP 只允许写 `memory/*` 和 `skills/*/SKILL.md`。
- Dream 不自动修改 Core、Providers、Channels、配置加载、启动脚本。
- 每次 Dream 必须记录 changelog、处理 cursor、修改摘要。
- 反思结果必须可人工阅读、可回滚、可禁用。

## 2. 关键架构原则

- Agent 语义判断优先：智能决策交给 Provider/LLM，工程规则只处理确定性边界，不把 Orison 做成“分数游戏”。
- Core 主体按 `nanobot` 思路设计：小核心、清晰模块、长期可维护。
- Channels 按 `cc-connect` 思路设计：平台边界清楚，Go 侧不理解 provider。
- Dream 按 `hermes-agent` 思路设计：从经验中学习，但写入范围受限。
- Core 只依赖统一 Provider 接口，不直接依赖 Codex CLI。
- `openai-codex` 只是第一个 provider，后续可增加 `openai`、`deepseek`、`kimi`、`local-openai-compatible` 等。
- Go Channels 只调用 Python Core API。
- Memory 独立于 Codex thread 历史，provider 切换后仍可复用。
- Skills 不绑定 Codex，未来 API provider 也能复用。
- MVP 优先可用闭环，避免 Web UI、多平台、向量库、复杂审批等重型能力。

## 3. Phase 0：项目骨架与边界

目标：把空项目整理成清晰的双语言架构，并在计划和代码里固定三类参考项目的职责边界。

参考：

- Python Core 目录借鉴 `../nanobot/nanobot/*` 的模块分层。
- Go Channels 目录借鉴 `../cc-connect/cmd`、`../cc-connect/core`、`../cc-connect/agent` 的入口/边界思想。
- Dream 目录先预留，后续借鉴 `../hermes-agent/agent/memory_manager.py`、`../hermes-agent/agent/skill_*`、`../hermes-agent/cron`。

要做：

1. 创建 Python Core 结构：
   - `orison/core`
   - `orison/providers`
   - `orison/providers/openai_codex.py`
   - `orison/memory`
   - `orison/skills`
   - `orison/cron`
   - `orison/dream`
   - `orison/server.py`
2. 创建 Go Channels 结构：
   - `cmd/orison-channel`
   - `channels/core`
   - `channels/client`
   - `channels/local`
   - `channels/qq`
3. 创建配置和文档：
   - `config.toml`
   - `.env.example`
   - `README.md`
4. 定义配置基础字段：
   - `provider.default = "openai-codex"`
   - `[providers.openai-codex]`
   - `model`
   - `reasoning_effort`
   - `sandbox`
   - `workdir`
   - `extra_args`
   - `orison.data_dir`

验收标准：

- `python3 -m orison.server` 能启动。
- `go run ./cmd/orison-channel` 能启动。
- 配置文件能被加载。
- Core 代码里没有散落的 `codex exec` 调用。
- `docs/plan.md` 明确写出 `nanobot`、`cc-connect`、`hermes-agent` 的参考职责。

进入下一阶段标准：

- Provider 抽象层已经存在。
- Codex CLI 被封装为 provider 实现，而不是架构本身。
- 后续实施时不会把 Core、Channels、Dream 三类职责混在一起。

## 4. Phase 1：Provider 接口设计

目标：设计一个足够小但可扩展的 Provider 接口，Core 风格参考 `nanobot` 的 provider 层，但保持 Orison MVP 所需的最小抽象。

要做：

1. 定义 `Provider` 接口：
   - `start_session(request) -> ProviderRunResult`
   - `resume_session(provider_session_id, request) -> ProviderRunResult`
   - `stream(request) -> async iterator[ProviderEvent]`
   - `healthcheck() -> ProviderHealth`
2. 定义 `ProviderRequest`：
   - `messages`
   - `system_context`
   - `session_id`
   - `workspace`
   - `model`
   - `metadata`
3. 定义 `ProviderEvent`：
   - `session_started`
   - `text_delta`
   - `status`
   - `tool_use`
   - `tool_result`
   - `final`
   - `error`
4. 定义 `ProviderRunResult`：
   - `content`
   - `provider_session_id`
   - `raw_events`
   - `usage`
   - `error`
5. 定义 `ProviderRegistry`：
   - 根据 config 创建 provider。
   - 支持默认 provider。
   - 支持多个 provider 配置。

验收标准：

- Python Core 只处理统一事件和统一结果。
- `openai-codex` 能适配该接口。
- 新增 provider 不需要改 session、memory、cron、dream、Go Channels。
- 接口没有照搬 nanobot 的完整 LLM/tool 复杂度，只保留 Orison 必须的扩展点。

## 5. Phase 2：实现 `openai-codex` Provider

目标：学习 `../nanobot` 的 Codex OAuth provider：只使用 Codex / ChatGPT OAuth 登录态，运行时直接调用 Codex Responses API，不启动 Codex CLI Agent，也不通过 `codex exec` / `codex exec resume` 继承 CLI 的项目规则、Agent 上下文或会话历史。

参考：

- OAuth 登录态、token 刷新和 token 文件位置参考 `nanobot provider login openai-codex` 与 `oauth-cli-kit`。
- HTTP/SSE 请求体、header、Responses API 事件消费参考 `../nanobot/nanobot/providers/openai_codex_provider.py` 和 `openai_responses/*`。
- provider registry 和错误归一化参考 `nanobot`，但 Orison 只保留自己需要的轻量 `ProviderRequest` / `ProviderEvent` / `ProviderRunResult` 边界。
- Go Channels 仍只调用 Python Core API，不知道 Codex、OAuth 或 Responses API 细节。

要做：

1. 实现 `OpenAICodexProvider` OAuth 路径：
   - 通过 `oauth_cli_kit.get_token()` 读取/刷新 Codex OAuth token。
   - token 默认来自 `~/.local/share/oauth-cli-kit/auth/codex.json`，也可由 `OAUTH_CLI_KIT_TOKEN_PATH` 覆盖。
   - 如果 token 不存在，提示运行 `nanobot provider login openai-codex` 或等价的 oauth-cli-kit 登录流程。
2. 运行时直接调用：
   - `POST https://chatgpt.com/backend-api/codex/responses`
   - header 包含 `Authorization: Bearer <access>`、`chatgpt-account-id`、`OpenAI-Beta: responses=experimental`、`originator: orison`。
   - body 使用 `store=false`、`stream=true`、`instructions`、`input`、`reasoning.effort`、`prompt_cache_key` 等 Responses API 字段。
3. 明确禁止 provider 启动 Codex CLI：
   - 不检查 `codex` 是否在 PATH。
   - 不调用 `codex exec --help`。
   - 不调用 `codex exec`。
   - 不调用 `codex exec resume`。
4. SSE 事件转换：
   - `response.created` / `response.completed` 中的 response id 作为 Orison 的 `provider_session_id` 记录。
   - `response.output_text.delta` 转成 Orison `text_delta`。
   - 完成后产出 Orison `final`。
   - `response.failed` / `error` 转成 Orison `error`。
   - 原始 Responses API 事件保留到 `raw_events`，便于排查 usage、限流、认证问题。
5. 配置支持：
   - `model`
   - `reasoning_effort`
   - 旧的 `sandbox`、`workdir`、`full_auto`、`extra_args` 字段可暂时保留用于配置兼容，但 OAuth Responses API provider 不应把它们转换成 CLI 参数。

验收标准：

- 能完成一轮 Codex Responses API 对话。
- provider 不会启动任何 `codex` 子进程。
- 能拿到并保存 response id 作为 provider session marker。
- OAuth token 不存在、过期刷新失败、HTTP 认证失败都有清晰错误。
- 原始 SSE 事件和 usage 可用于排查。
- Go 代码里仍没有直接调用 Codex CLI。
- 短消息不会再携带 Codex CLI Agent 的项目规则、CLI 会话和非 Orison 上下文。

## 6. Phase 3：Python Core 会话层

目标：把 provider 包装成 Orison 自己的 Agent Core。Core 主体借鉴 `nanobot` 的 session、context、runner 思路。

要做：

1. 实现 `SessionManager`：
   - Orison session id。
   - provider name。
   - provider session id。
   - channel。
   - user id。
   - created/updated timestamps。
2. 实现 Core API：
   - `GET /v1/health`
   - `GET /v1/providers`
   - `GET /v1/sessions/{session_id}`
   - `POST /v1/sessions/{session_id}/messages`
3. 实现 context 组装：
   - 用户消息。
   - Orison system context。
   - memory 摘要。
   - skills 摘要。
   - channel metadata。
   - 涉及“该注入哪些可选上下文”的判断时，优先让 Agent/LLM 基于语义选择，避免把多语言用户意图压成关键词分数。
4. 落盘运行记录：
   - user message。
   - provider events。
   - final response。
   - error。
   - provider session id。

验收标准：

- HTTP API 可以完成多轮对话。
- Core 重启后仍能找到 provider session id。
- 每轮输入、输出、错误和 provider events 都有记录。
- 更换 provider 不需要改 API 层。
- Go Channels 只依赖 HTTP API，不依赖 Python 内部对象。

## 7. Phase 4：Memory MVP

目标：建立 Orison 自己的长期记忆，独立于 Codex thread 历史。

参考：

- 存储和 session 关系参考 `nanobot` 的轻量本地持久化。
- memory context 注入、安全 fence、长期用户模型参考 `hermes-agent`。

要做：

1. SQLite 存储：
   - `sessions`
   - `messages`
   - `provider_runs`
   - `provider_events`
   - `memory_events`
2. Markdown 长期记忆：
   - `memory/USER.md`：用户偏好和背景。
   - `memory/MEMORY.md`：长期事实、项目经验、稳定决策。
   - `memory/SOUL.md`：Agent 行为准则。
3. 每轮注入：
   - 用户偏好摘要。
   - 长期事实摘要。
   - Agent 行为准则。
   - 用明确 fence 标记为 recalled context，避免被当成新用户输入。
   - Memory MVP 可以先全量注入小型 Markdown 摘要；未来做选择性 memory retrieval 时，不以英文关键词打分作为主要路径，应由 Agent/LLM 结合用户语义、历史和记忆摘要判断。
4. 基础管理能力：
   - 追加 memory。
   - 搜索历史。
   - 查看当前 memory。

验收标准：

- 用户偏好可以写入 `USER.md`。
- 新 session 能读取旧 memory。
- Codex thread 历史和 Orison memory 分离。
- memory 文件人工可读、可编辑、可备份。
- provider 输出不会把 memory fence 原样污染到长期记忆。

## 8. Phase 5：Skills MVP

状态：已完成并验收。

目标：建立 Orison 自己的可复用能力库。

参考：

- skill 文件和加载策略参考 `nanobot` 的 skills。
- 从经验中沉淀 skill 的后续入口参考 `hermes-agent`。

要做：

1. 定义 skill 格式：
   - `skills/<name>/SKILL.md`
   - frontmatter 至少包含 `name` 和 `description`
2. 实现 `SkillRegistry`：
   - 扫描 skills。
   - 提取 metadata。
   - 输出 skills 摘要。
   - 按需加载全文。
3. 上下文策略：
   - 默认只注入 skills 摘要。
   - 由 Agent/LLM 基于用户消息、channel metadata 和 skills 摘要自主判断相关 skill，再注入具体 skill 全文。
   - 不要用英文关键词匹配、硬编码词表或简单字符串规则作为主要 skill 路由方式；用户输入可能是中文或其他语言，规则会天然漏判。
   - 规则最多只能作为 provider/LLM 不可用时的保守 fallback，且不能替代 Agent 的语义判断。
   - 避免每轮塞入所有 skill。
4. 初始 skills：
   - `coding`
   - `planning`
   - `reflection`
   - `memory-maintenance`

验收标准：

- 新增 `skills/foo/SKILL.md` 后自动被发现。
- Provider 能收到相关 skill 上下文。
- Skills 不绑定 Codex，未来 API provider 也能复用。
- 上下文不会因 skills 过多明显膨胀。
- 非英文用户输入也能通过 Agent/LLM 语义判断选择合适 skill，而不是依赖英文关键词命中。

## 9. Phase 6：Go Channels MVP

状态：已完成并验收。

目标：用 Go 做消息入口层，严格借鉴 `cc-connect` 的平台边界：channel 只做消息接入、session key、平台元数据和投递。MVP 外部聊天通道明确实现 QQ（NapCat / OneBot v11），不实现 QQ 官方机器人平台。

参考：

- QQ 通道借鉴 `../cc-connect/platform/qq/qq.go` 的 OneBot v11 Forward WebSocket 模式。
- QQ 接入文档借鉴 `../cc-connect/docs/qq.md`：NapCat 作为推荐部署方式，其他 OneBot v11 实现作为兼容目标。
- 只借鉴平台 adapter 边界和消息解析/投递方式，不照搬 cc-connect 的 agent 插件系统。

要做：

1. 定义 Go 核心类型：
   - `Message`
   - `Event`
   - `Channel`
   - `Handler`
2. 实现 Python Core client：
   - 调用 `/v1/sessions/{session_id}/messages`
   - 解析 final response。
   - 后续预留 SSE。
3. 实现 local channel：
   - 终端输入。
   - 终端输出。
   - 稳定 session id。
4. 实现 HTTP webhook channel：
   - `POST /message`
   - 输入 `user_id`、`text`
   - 输出 response。
5. 实现 QQ channel：
   - 新增 `channels/qq`。
   - 通过 OneBot v11 Forward WebSocket 连接 NapCat，默认 `ws://127.0.0.1:3001`。
   - 配置支持 `ws_url`、`token`、`allow_from`、`share_session_in_channel`。
   - 支持私聊和群聊消息，生成稳定 session key：
     - 私聊：`qq:{user_id}`
     - 群聊按用户隔离：`qq:{group_id}:{user_id}`
     - 群聊共享会话：`qq:g:{group_id}`
   - 解析 OneBot 文本消息；图片、语音等多媒体先保留 metadata 或降级提示，不阻塞文本 MVP。
   - 通过 OneBot `send_private_msg` / `send_group_msg` 投递 Core 返回文本。
   - 处理 NapCat 断连重连、重复消息、机器人自身消息过滤和 `allow_from` 白名单。

验收标准：

- `go run ./cmd/orison-channel` 可以和 Orison Core 对话。
- Go 层不直接调用 Codex。
- Go 层不包含 provider 逻辑。
- Python Core 不可用时 Go 侧错误清楚。
- QQ channel 能连接 NapCat Forward WebSocket。
- QQ 私聊消息能进入 Orison Core，并把 final response 发回 QQ。
- QQ 群聊能按配置选择按用户隔离或群共享 session。
- QQ channel 不包含 QQ 官方机器人平台 `qqbot` 逻辑。
- 后续增加平台只需新增 channel adapter。

## 10. Phase 7：Streaming 体验

状态：已完成并验收。

目标：让 provider 事件实时传到 channel，提升日常使用体验。

参考：

- Core event stream 参考 `nanobot` 的 streaming 和 event 分层。
- Channel 端分块输出、平台能力差异处理参考 `cc-connect`。

要做：

1. Python Core 支持 SSE：
   - `session_started`
   - `text_delta`
   - `status`
   - `tool_use`
   - `tool_result`
   - `final`
   - `error`
2. `openai-codex` provider：
   - Codex JSONL 转 Orison SSE。
   - 未识别事件保留 debug。
3. Go client：
   - 解析 SSE。
   - local channel 实时打印。
4. 断连处理：
   - 连接断开时清理子进程或标记 run 状态。

验收标准：

- Codex 运行中可以逐步看到输出。
- 工具或状态事件能被简短展示。
- 最终回答不重复打印。
- 错误能传到 Go 侧。

## 11. Phase 8：Cron MVP

目标：让 Orison 具备主动执行任务的能力。

参考：

- 轻量调度和 Core 内部触发参考 `nanobot`。
- 定时任务结果投递到不同会话/平台的思路参考 `hermes-agent`。

要做：

1. 定义 cron job：
   - `id`
   - `name`
   - `prompt`
   - `schedule`
   - `provider`
   - `target_session`
   - `enabled`
   - `last_run_at`
   - `next_run_at`
   - `last_status`
2. 支持 schedule：
   - `once:<iso_time>`
   - `interval:30m`
   - `daily:09:00`
3. 运行方式：
   - 到点后调用 Core session flow。
   - 默认用 `provider.default`。
   - job 可覆盖 provider。
4. API：
   - list
   - create
   - pause
   - resume
   - delete
   - trigger_now

验收标准：

- 可以创建定时任务。
- 到点后自动调用 `openai-codex` provider。
- 结果写入 session。
- 失败任务记录错误，不影响 Core。
- 未来切换 provider 不需要改 cron 逻辑。

## 12. Phase 9：Dream 自反思 MVP

目标：从使用历史中沉淀长期记忆和 reusable skills。该阶段的主要参考对象是 `../hermes-agent`。

要做：

1. Dream 输入：
   - 最近未处理消息。
   - provider run 结果。
   - 失败案例。
   - 当前 memory。
   - 当前 skills 列表。
2. Dream 执行：
   - 默认使用 `openai-codex` provider。
   - 未来可配置其他 provider。
   - 第一版只允许修改 memory 和 skills。
3. Dream 可写范围：
   - `memory/USER.md`
   - `memory/MEMORY.md`
   - `memory/SOUL.md`
   - `skills/*/SKILL.md`
4. Dream 禁止范围：
   - `orison/core`
   - `orison/providers`
   - `channels`
   - `cmd`
   - 配置加载和启动入口
5. Dream 输出：
   - changelog。
   - cursor。
   - 修改摘要。
   - rejected changes。
6. 触发方式：
   - 手动 API。
   - Cron 每日一次。

验收标准：

- Dream 能从历史中提取稳定用户偏好。
- Dream 能从重复任务中沉淀简单 skill。
- Dream 不修改核心代码。
- 每次运行有 changelog。
- 已处理历史不会重复处理。
- 用户能人工审计 Dream 对 memory 和 skills 的修改。

## 13. Phase 10：未来 Provider 扩展准备

目标：MVP 不实现其他 provider，但保证后续自然接入。

要预留：

1. 后续 provider 文件：
   - `orison/providers/openai.py`
   - `orison/providers/deepseek.py`
   - `orison/providers/kimi.py`
   - `orison/providers/local_openai_compatible.py`
2. API provider 需要适配：
   - messages。
   - tool calling。
   - streaming。
   - usage。
   - error normalization。
3. Core 不承担：
   - provider 私有认证细节。
   - provider 私有事件格式。
   - provider 私有 tool call 格式。

验收标准：

- 新增 provider 只需新增 provider 文件并注册。
- Core、Memory、Cron、Dream、Go Channels 不需要重写。
- `openai-codex` 是普通 provider 实现，不是特殊分支。

## 14. Phase 11：MVP 收口与稳定化

目标：达到可日常自用状态。

要做：

1. 测试：
   - ProviderRegistry 测试。
   - `openai-codex` provider mock 测试。
   - session/provider session 映射测试。
   - memory 注入测试。
   - skills 扫描测试。
   - cron 触发测试。
   - dream cursor 测试。
   - Go channel 到 Python Core 集成测试。
2. 文档：
   - 三个参考项目的职责说明。
   - `codex login` 配置说明。
   - Codex sandbox/model 配置说明。
   - Orison Core 启动说明。
   - Go Channel 启动说明。
   - Memory、Skills、Dream 使用说明。
3. 运维体验：
   - 清晰 log。
   - 健康检查。
   - 失败错误可读。
   - 数据目录可备份。

验收标准：

- 从 README 可以完整启动 Orison。
- 可以通过 Go local channel 对话。
- 可以 resume 同一 provider session。
- 可以读取 memory。
- 可以使用 skills prompt。
- 可以创建 cron。
- 可以运行 dream。
- 测试通过。

MVP 完成标准：

- Orison 可以作为日常自用 Agent。
- Core 的主体设计继承 `nanobot` 的轻量 Agent 架构。
- Channels 的消息边界继承 `cc-connect` 的 platform/agent 解耦。
- Dream 的学习闭环继承 `hermes-agent` 的 memory/skills 自进化思想。
- 它通过 `openai-codex` provider 使用 `codex login` 登录态。
- 它具备会话、channels、memory、skills、cron、dream 的完整闭环。

## 15. MVP 明确不做

第一版不做：

- DeepSeek/OpenAI/Kimi API provider 实现。
- 本地模型 provider。
- 多平台聊天适配。
- Web UI。
- MCP 完整生态。
- 向量数据库。
- 多用户权限系统。
- 自动修改核心代码的自进化。
- 复杂审批系统。

这些能力等 MVP 稳定后再逐步增加，但当前 Provider 架构必须避免将 Core 绑定死在 Codex CLI 上。
