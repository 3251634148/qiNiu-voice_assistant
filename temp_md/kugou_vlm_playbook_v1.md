# KuGou VLM Playbook v1（持久化规则）

> 目标：让 VLM 在**一次推理**中输出 `UI_PLAN_JSON`（先隐式判断 state，再给 action/bbox），并通过**硬约束校验**确保正确性。
>
> 关键原则：
> - **state 决定允许的 target**；模型即使“看到了搜索入口”，只要 state 不满足，也必须先收敛。
> - **不允许编造目标**：bbox 过小/越界/与 state-target 不一致将被判定为不合法，并触发一次纠错重问。
> - **图片输入统一为 WebP**（quality=95），用于降低传输与视觉编码成本。

---

## 1. 状态机（state）

### 1.1 state 枚举

- **not_music_main**：左侧侧边栏当前高亮不是“音乐”（常见：高亮“我的”）。
- **music_main**：已处于音乐主界面（具备搜索入口的可能）。
- **search_view**：已进入搜索页（通常出现“取消/历史搜索/输入框”等）。
- **results_view**：已进入搜索结果页（通常出现“单曲”等 tabs 或结果列表结构）。
- **overlay_panel**：右侧半边栏/抽屉遮挡（导致主界面关键入口不可见）。
- **overlay_fullscreen**：内容区全屏/详情页遮挡（占满内容区，左上角通常有返回）。
- **playing**：已触发播放（可先弱判定：底栏出现播放态/进度在走/出现暂停按钮等）。
- **unknown**：无法可靠判断。

### 1.2 状态判定的“证据”要求（模型输出 evidence 用）

模型必须给出最多 3 条 `evidence`，用于说明为何判定该 state（例如：
- `sidebar_active=我的`
- `sidebar_active=音乐`
- `search_ui_cancel_visible`
- `results_tab_single_visible`
- `overlay_detected_panel`
- `overlay_detected_fullscreen`
- `bottom_player_playing`
)

> 注意：`evidence` 是为 debug 与纠错准备的；不要求百分百严格，但必须自洽。

---

## 2. 目标（target）

### 2.1 target 枚举

- **sidebar_music**：左侧侧边栏“音乐”入口（或其图标）。
- **search_entry**：音乐主界面顶部“搜索入口”（搜索框/放大镜/搜索按钮）。
- **back_button**：返回按钮（用于退出遮挡面板/全屏页）。
- **search_input**：搜索页顶部输入框。
- **submit_search**：提交搜索（回车也算 submit）。
- **tab_single**：结果页“单曲”tab。
- **song_item**：目标歌曲条目（与 query 匹配）。
- **unknown**。

---

## 3. 硬约束：state → 允许的 target（必须严格遵守）

> 你反馈的变更点已纳入：**state=results_view 只允许 tab_single / song_item**。

- **state=not_music_main**：只允许 `sidebar_music` 或 `back_button`
  - 禁止：`search_entry/search_input/submit_search/tab_single/song_item`

- **state=music_main**：只允许 `search_entry` 或 `back_button`

- **state=search_view**：只允许 `search_input` 或 `submit_search`

- **state=results_view**：只允许 `tab_single` 或 `song_item`

- **state=overlay_panel**：只允许 `back_button`

- **state=overlay_fullscreen**：只允许 `back_button`

- **state=playing**：只允许 `noop`（且 `done=true`）

- **state=unknown**：只允许 `back_button` 或 `unknown`（优先收敛；若需停机请用 action=noop）

---

## 4. 搜索并播放流程（按 state 选择下一步策略）

### 4.1 not_music_main → 切到音乐主界面

- **目标**：进入 `music_main`
- **动作**：`click` + `target=sidebar_music`
- **幂等**：允许。如果模型不确定是否已高亮音乐，可 `idempotent_ok=true`。

### 4.2 music_main → 进入搜索页

- **目标**：进入 `search_view`
- **优先动作**：`click` + `target=search_entry`
- **若看不到 search_entry**：
  - 判为遮挡：输出 `click` + `target=back_button`
  - 返回后重新截图再判 state，直到 `search_entry` 可见

### 4.3 search_view → 输入并提交搜索

- **目标**：进入 `results_view`
- **动作**：
  - `type_text` + `target=search_input` + `text=<query>`
  - `submit=true`
- **固定输入策略**：执行 `type_text` 前，程序会自动 `Cmd+A + Delete` 清空。

### 4.4 results_view → 切单曲并点歌

- **动作序列**（每次只输出一步）：
  1) 若“单曲”tab 未选中，优先 `click` + `target=tab_single`
  2) `click` + `target=song_item`（与 query 匹配的曲目）

> 说明：本版本不强制在 results_view 返回 back_button（按你的约束）。如未来需要“结果页返回”的能力，应新增 state 或放宽约束。

### 4.5 overlay_panel / overlay_fullscreen → 退出遮挡

- **目标**：回到可见 `search_entry` 或 `search_input` 的页面
- **动作**：`click` + `target=back_button`

### 4.6 playing → 停机

- **动作**：`noop` + `done=true`

---

## 5. 单次推理输出协议：UI_PLAN_JSON（v1）

模型必须只输出一行 JSON（不得包含额外解释文本），并以 `UI_PLAN_JSON` 前缀开头：

UI_PLAN_JSON{
  "state": "not_music_main|music_main|search_view|results_view|overlay_panel|overlay_fullscreen|playing|unknown",
  "state_confidence": 0.0,
  "action": "click|type_text|noop",
  "target": "sidebar_music|search_entry|back_button|search_input|submit_search|tab_single|song_item|unknown",
  "bbox_norm": {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0},
  "text": "",
  "submit": false,
  "done": false,
  "idempotent_ok": false,
  "action_confidence": 0.0,
  "evidence": ["...", "...", "..."]
}

---

## 6. 执行前硬校验（driver 必须执行）

### 6.1 bbox 规则

- 必须满足：\(0 \le x,y,w,h \le 1\)，且 \(x+w \le 1\)，\(y+h \le 1\)
- **面积阈值**：\(w \times h \ge 0.002\)（默认，可调）

### 6.2 state-target 一致性

- 按第 3 节的 state→target 表严格校验。
- 不一致即判不合法，并触发一次纠错重问（给出失败原因）。

### 6.3 置信度门槛

- 若 `state_confidence < 0.55` 或 `action_confidence < 0.55`：判为不可靠，触发纠错重问。

---

## 7. 图像输入规范（WebP only）

### 7.1 目标

- 送入 VLM 的图片必须是 **WebP**，并使用 **quality=95** 的“视觉无损”压缩策略。

### 7.2 处理管线

- **窗口截图**：系统可能先产出 PNG（受 `screencapture` 限制），随后必须转换为 WebP：
  - Resize（限制最长边，例如 512）
  - Encode WebP（quality=95）
  - 送入 Ollama 的 `messages[].images` 使用 **WebP 文件内容的 base64**

> 注意：不提供 PNG/JPEG 自动兜底。若 WebP 转换失败或环境缺少编码工具，driver 必须：
> - 明确抛错中止
> - 在 `ui_debug/<requestId>/` 落盘错误原因与输入图片路径，便于排障

### 7.3 建议的预检（可选）

- 启动或首次进入 VLM 模式时做一次 WebP 编码能力预检（例如调用本地编码工具生成 1 张 webp）。
- 预检失败则直接提示“缺少 WebP 编码能力”，避免跑到中途才失败。

---

## 8. 可观测性（必须落盘）

每一步必须落盘到 `~/Documents/VoiceAssistant/ui_debug/<requestId>/`：

- 输入原始截图（PNG 或系统产物）
- 送入模型的 WebP（quality=95）
- request JSON（包含 playbook 版本、最近步骤摘要、query）
- response JSON（原始文本 + 解析后的 `UI_PLAN_JSON`）
- 校验结果（pass/fail + fail reason）
- 如触发纠错重问：第二次 request/response 与 fail reason
