## 2026-02-11 - 为语音助手增加“外界信息访问（联网检索/天气/新闻/时间）”能力的迭代方案

### 背景与现状（基于当前仓库）
- **Python 后端主链路已具备完整的工具体系**：`LLMService` 负责模型调用与 tools schema；`ConversationController` 负责解析 `INTENT_JSON` 与 `toolCalls`、触发 `SafetyService` 风险评估与确认；`ToolRouter` 负责按 requestId 设置 `VOICE_ASSISTANT_DEBUG_RUN` 并执行具体工具，调试产物落盘到 `~/Documents/VoiceAssistant/ui_debug/<requestId>/`。
- 当前 `backend_py/` 的工具集合**不包含任何联网/外部信息工具**，因此模型只能“凭训练数据回答”，无法可靠回答当前时间、最近新闻、天气等实时信息。

### 目标（这轮迭代的范围）
让本地项目中的 LLM 具备**可控、可审计**的外界信息访问能力，并满足：
- **可扩展**：后续可以很容易新增更多信息源（例如：财经、汇率、股价、航班、百科、地图）。
- **安全**：避免泄露本地隐私（路径、环境变量、日志、用户敏感内容）；避免模型把工具输出当作“绝对正确”。
- **可观测**：每次检索/查询都落盘证据（请求、响应摘要、引用来源、耗时、错误原因）。

### 两种落地路线（建议先 A 后 B）

#### 路线 A：直接实现“外界信息工具”（推荐先做）
**特点**：开发成本低、链路清晰、便于快速上线；后续再演进为 MCP 插件化。

- **新增工具（建议 1 个统一工具，减少模型选择复杂度）**
  - 工具名：`external_info`
  - 参数：
    - `type`: `"web_search" | "weather" | "news" | "time"`
    - `query`: string（对 `web_search/news` 必填；`weather` 可选）
    - `location`: string（`weather` 可选，建议用户显式提供城市）
    - `topK`: number（可选，默认 5，最大 10）
    - `freshness`: `"day" | "week" | "month" | "year" | "all"`（可选，仅 `web_search/news`）
    - `language`: string（可选，默认 `zh-CN`）

- **信息源建议**
  - `web_search`：Brave Search API（需要 key）或 SerpAPI（需要 key）二选一。
  - `weather`：Open-Meteo（可免 key，但需要经纬度；若用户给城市名，可用 Nominatim/地名解析，注意速率限制与缓存）。
  - `news`：优先用搜索实现（freshness=day/week + 站点过滤）；后续再加 RSS/媒体 API。
  - `time`：本地时间（`datetime.now()` + `zoneinfo`），无需联网。

- **安全策略**
  - `external_info` 归类为**中风险**：默认不需要确认，但必须做“出站内容过滤”。
  - **出站过滤（必须）**：禁止把以下信息拼进 query：
    - 绝对路径（如 `/Users/...`）、`VOICE_ASSISTANT_DEBUG_RUN`、`DASHSCOPE_API_KEY` 等疑似密钥、日志片段大段文本。
  - **隐私提示**：当用户问天气但未提供城市时，只允许提 1 个澄清问题：城市/地区；不做 IP 定位默认行为。

- **可观测性（必须落盘）**
  - 产物文件（示例）：
    - `external_info_request_*.json`：请求参数（已脱敏）
    - `external_info_response_*.json`：命中条目（title/url/snippet）、耗时、provider 元信息
    - `external_info_error_*.json`：异常堆栈与错误分类

- **对 LLM 的提示词策略**
  - 在 `LLMService.system_prompt` 增加明确规则：
    - 涉及“当前时间/今天日期/最近新闻/实时天气/刚发生的事件”时，必须调用 `external_info`，禁止编造。
    - 输出时要把信息来源以“口语化引用”的方式表达（例如“我查到…来源是…”，而不是 Markdown）。

- **对前端的改动（可选）**
  - 现有 `tool-result` 事件已能承载 `result.details.sources`，前端可后续再做“引用来源展示”。第一期不阻塞。

#### 路线 B：引入 MCP 插件化（第二阶段）
**特点**：通过 MCP 将“外界信息能力”做成可插拔工具源，未来接入更多服务成本更低。

- 在 `backend_py/` 增加一个轻量 `McpClientManager`：
  - 支持配置多个 MCP server（stdio/HTTP）。
  - 只允许访问白名单工具（例如 `brave_web_search`、`open_meteo_weather`）。
  - 每次调用同样落盘请求/响应。
- LLM 侧仍只暴露一个统一工具 `external_info`（或 `mcp_call`），由服务端把它路由到 MCP。
- 风险：引入 SDK/协议实现与调试成本增加；因此建议先用路线 A 把能力跑通。

### 分阶段迭代里程碑（建议）

#### Phase 1（最小可用）：web_search + time
- 新增 `external_info(type=web_search|time)`
- 增加 provider：Brave Search（或 SerpAPI）
- 调试产物落盘与基础脱敏
- 添加 smoke 脚本：`test_scripts/test_external_info_smoke.py`

#### Phase 2：weather + news
- `weather`：城市→经纬度解析 + Open-Meteo
- `news`：基于搜索 freshness + 站点过滤
- 增加缓存（TTL 60s~300s）与并发限制（避免触发 API rate limit）

#### Phase 3：MCP 化与引用展示
- 引入 MCP client（如需要）
- 前端把 sources 以“引用卡片/可复制链接”展示

### 需要你确认的关键决策（实现前必须定稿）
1. **搜索 provider**：Brave Search API vs SerpAPI（你更倾向哪个？是否已有 key？）
2. **天气定位策略**：
   - 只接受用户显式城市（最安全）
   - 允许 IP 定位（更省事但隐私更敏感，需要开关）
3. **`external_info` 是否需要确认弹窗**：
   - 默认不需要（推荐）
   - 或首次联网需要一次性授权（更安全但交互多一步）

### 预计修改范围（文件级别）
- `backend_py/services/llm_service.py`：新增工具 schema + 提示词策略更新
- `backend_py/services/tool_router.py`：新增 `external_info` 路由分支
- `backend_py/safety.py`：新增 `external_info` 风险分类与校验
- `backend_py/config.py`：新增 provider key 与开关配置
- `backend_py/services/external_info_service.py`（新增）：封装联网请求、脱敏、缓存与落盘
- `test_scripts/test_external_info_smoke.py`（新增）：端到端或单模块 smoke
- `docs/CHANGELOG.md`：在你确认并落地代码后再更新

---

### 本次对话已落地实现（Phase3：Qwen tools 决策 + 本地 Client 执行）

#### 已实现能力
- **工具集合（联网信息工具）**：`web_search`（SerpAPI）、`get_latest_news`（newsdata.io）、`get_ip_location`（ipinfo widget demo）、`get_weather_now`（QWeather）、`get_current_time`。
- **本土化默认配置**：
  - SerpAPI：`google_domain=google.com.hk`、`gl=cn`、`hl=zh-cn`、`safe=active`。
  - newsdata：默认 `country=cn&language=zh`。
- **天气查询策略**：
  - 默认允许 IP 定位（通过 `get_ip_location` 获取 `lon_lat`）。
  - 同时支持显式城市名：`get_weather_now(location="北京")` 会先用 SerpAPI 解析经纬度，再查询 QWeather。
- **并行工具调用**：当 Qwen 返回多个联网工具 `tool_calls` 时，后端用 `asyncio.gather()` 并行执行并回填。

#### 安全与授权
- **前端新增“联网开关”（默认关闭）**，首次开启会弹出一次性授权确认；用户确认后才会持久化并同步到后端会话态。
- **后端仅在会话 `network_access_enabled=true` 时**才会把联网工具暴露给模型（不启用时模型无法调用这些工具）。

#### 可观测性（ui_debug 证据落盘）
- 每次联网工具调用都会在 `ui_debug/<requestId>/` 下写入：
  - `net_tool_request_<tool>_*.json`
  - `net_tool_response_<tool>_*.json`
  - `net_tool_error_<tool>_*.json`

#### 代码与配置（摘要）
- 后端新增：`backend_py/services/network_tools_service.py`（工具实现与证据落盘）。
- 后端改造：`LLMService` 支持动态注入工具 + `parallel_tool_calls`；`ConversationController` 新增联网 tool-loop；`main.py` 新增 `update-network-settings` 事件。
- 前端改造：`SettingsModal` 增加联网开关与一次性授权弹窗，并持久化到 `localStorage`。
- 测试脚本：新增 `test_scripts/test_network_tools_smoke.py`（可选联网验证）。

#### 环境变量（需要你在本机配置，不写入代码/日志）
- `SERPAPI_API_KEY`
- `NEWSDATA_API_KEY`
- `QWEATHER_API_KEY`
- `QWEATHER_API_HOST`

