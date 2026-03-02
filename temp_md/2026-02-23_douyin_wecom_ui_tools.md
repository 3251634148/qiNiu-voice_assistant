## 2026-02-23 - 抖音/企业微信 OCR-first UI 工具接入记录

### 背景与目标
- 新增两个独立 UI 自动化工具：`douyin_ui` 与 `wecom_ui`。
- 目标是让 LLM 能通过 `INTENT_JSON.actions` 触发工具调用，并由后端完成高风险确认、可观测证据落盘与执行。

### 工具能力
- `douyin_ui`
  - 能力：打开抖音（macOS App）→ 搜索 `query` → 切到“视频” → 在结果内容区按 OCR 匹配并点击播放。
  - 参数：`query`（必填）、`debug`（可选）、`dryRun`（可选）。
- `wecom_ui`
  - 能力：打开企业微信 → 优先从左侧会话列表直达联系人；若失败则 `Cmd+F` 聚焦顶部搜索框并回车触发搜索 →（必要时切到“联系人”tab）→ 点击最匹配联系人进入会话 → **标题区 OCR 校验命中联系人后**按 Enter 发送 → 恢复草稿与剪贴板。
  - 参数：`contactName`（必填）、`message`（必填）、`debug`（可选）、`dryRun`（可选）。

### 风控与证据
- 两工具在 `SafetyService` 中标为 **high risk**，默认 **requires_confirmation=true**。
- `ToolRouter` 会按 `requestId/toolCallId` 写入 `VOICE_ASSISTANT_DEBUG_RUN`，使截图/OCR/JSON 产物进入：
  - `~/Documents/VoiceAssistant/ui_debug/<runId>/`

### ROI 环境变量（可选覆盖）
- 抖音：
  - `DOUYIN_SIDEBAR_ROI`
  - `DOUYIN_TOP_SEARCH_ROI`
  - `DOUYIN_RESULTS_TABS_ROI`
  - `DOUYIN_RESULTS_CONTENT_ROI`
- 企业微信：
  - `WECOM_LEFT_NAV_ROI`
  - `WECOM_CHAT_LIST_ROI`
  - `WECOM_CHAT_HEADER_ROI`
  - `WECOM_SEARCH_POPUP_ROI`
  - `WECOM_GLOBAL_TABS_ROI`
  - `WECOM_RESULTS_LIST_ROI`

### 可复现调试脚本
- 抖音（dry-run）：
  - `VOICE_ASSISTANT_DEBUG_RUN=douyin_dry_$(date +%s) backend_py/.venv/bin/python test_scripts/debug_douyin_search_play_flow.py --dry-run --query "甄嬛传 解析"`
- 企业微信（dry-run）：
  - `VOICE_ASSISTANT_DEBUG_RUN=wecom_dry_$(date +%s) backend_py/.venv/bin/python test_scripts/debug_wecom_search_send_flow.py --dry-run --contact "顾老师" --message "你好"`

### 已知限制
- 两工作流仍属于“UI 版本敏感”能力：若 OCR 识别不到关键锚点，需要通过 ROI env 做小幅调参。
- 抖音流程目前未全面接入全部快捷键（已优先使用 OCR 锚点 + 偏移点击以降低误点）。
