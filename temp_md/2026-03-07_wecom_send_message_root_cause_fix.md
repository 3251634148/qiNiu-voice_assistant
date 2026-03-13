## 2026-03-07（根因修复 — 企微发送消息误判“未进入会话”）

### 背景与现象

- **requestId**：`e86aa33c-28b7-4fb6-812e-ed98a3e923fc`
- **前端错误**：`❌ 工具执行失败: 未能进入目标联系人会话，已中止以避免误发`
- **人工观察到的异常行为**：
  - 左侧消息栏已存在目标联系人，但仍走全局搜索；
  - 全局搜索过程中未稳定切到“联系人”结果域，存在误识别风险；
  - 进入联系人后又重复进行全局搜索，且未触发发送步骤。

### 根因（以证据为准）

根因不是“没找到联系人”，而是 **OCR-first 工作流在两个关键阶段持续误判“未进入目标会话”**，触发了防误发护栏中止。

1) **会话列表直达阶段误判（directChatPick 误命中）**

- ui_debug 中记录到的 `directChatPick.text` 为：`"1. 不提供语音解析以及语"`，`similarity=0.0`。
- 该文本显然属于聊天正文/草稿内容，而不是左侧会话列表项。
- 这说明 **`chat_list` ROI 覆盖范围过大**，OCR 抽到了中间聊天区/底部输入区的正文长句，导致“直达会话”判断失败，退化为全局搜索兜底。

2) **全局搜索兜底阶段误判（header verify 误读标题区）**

- ui_debug 中三次 `globalSearchKeyboard[*].verified=false`，`headerPreview` 只读到 `"品 2022计算机科学与技术01"`，未读到目标联系人名。
- 结合截图人工可见标题区已切换到目标联系人，说明 **不是 UI 没进入会话，而是 `chat_header` ROI 偏移/覆盖不当导致 OCR 没读到联系人名**，从而连续校验失败。

### 修复方案（方案 A，最小化改动）

目标：**不降低“防误发护栏”**，只修复“候选选取与 ROI 过大导致的误判”。

1) **收紧默认 ROI**

- `chat_list`：收窄宽度与高度，避免吃进聊天正文与输入区，从源头减少“正文干扰文本”进入候选集合。
- `chat_header`：向左扩展起点并适度调整宽高，确保覆盖联系人名与其右侧辅助信息，避免只 OCR 到右侧文本而错过联系人名。

2) **增强噪声过滤 + 候选选择（可回归单测）**

- 将 `_is_noise_text` 与 `_best_match_box` 抽为 `WeComUIController` 的实例方法，便于测试与复用。
- 在噪声过滤中增加对：
  - 编号条目（如 `1.` / `2)` / `3、`）
  - 长句且包含明显标点的正文文本
  - 时间戳、草稿/图片/文件等标签
  的过滤，避免其参与联系人候选竞争。

### 代码改动

- 修改：`backend_py/services/wecom_ui_controller.py`
  - 调整默认 `WECOM_CHAT_LIST_ROI`、`WECOM_CHAT_HEADER_ROI`
  - 抽取并增强 `_is_noise_text()`、`_best_match_box()`，并在工作流中统一使用

### 回归测试

- 新增：`test_scripts/test_wecom_ui_controller_pick_and_noise.py`
  - 验证 `_is_noise_text()` 能过滤正文/编号条目等干扰文本
  - 验证 `_best_match_box()` 在噪声干扰下仍能选中目标联系人

测试结果：`3 passed`

### 2026-03-07（补充修复 — 发送后又回填联系人名/多一步键入）

#### 现象

- requestId：`5f195fc5-fa86-401d-9750-22a178f47937`
- 实际发送成功，但发送后输入框里又出现了 **“罗晨曦”**，看起来多了一步键入/粘贴。

#### 根因（以证据为准）

发送阶段的“草稿回填”逻辑会执行：

- `Cmd+A + Cmd+X` 试图剪切输入框草稿到剪贴板；
- 随后用 `pbpaste()` 读取剪贴板作为 `old_draft`；
- 发送完如果 `old_draft.strip()` 就把它再粘回输入框。

但在本次 requestId 的 ui_debug 中：

- `clipboardDigestBefore == oldDraftDigest == clipboardDigestAfter` 且 `length=3`
- 结合项目内 `text_digest("罗晨曦")` 的计算值一致，证明发送前剪贴板内容就是联系人名 **“罗晨曦”**，
  并且 `Cmd+X` 没有真正剪切到输入框草稿（剪贴板未变化），导致程序把剪贴板内容误当草稿并回填。

#### 修复（sentinel 哨兵判定，最小化改动）

目标：仅当 **确实从输入框剪切到了文本** 时才回填草稿，避免把剪贴板原内容误回填进输入框。

- 在剪切前先把剪贴板写入唯一 `sentinel`；
- 执行 `Cmd+A / Cmd+X` 后读取剪贴板：
  - 若仍等于 `sentinel`：判定未剪切到草稿（`cut_ok=False`），不回填；
  - 否则：判定剪切成功（`cut_ok=True`），允许回填。

#### 代码与测试

- 修改：`backend_py/services/wecom_ui_controller.py`
  - 新增 `_cut_chat_input_draft_with_sentinel()`，并在发送逻辑中用 `cut_ok` 保护回填分支
- 新增回归测试：`test_scripts/test_wecom_ui_controller_draft_restore_guard.py`

测试结果：`2 passed`

### 2026-03-07（补充修复 — 全局搜索必须切“联系人”tab，避免综合结果误进群聊）

#### 现象

- requestId：`11156f16-7ed8-4ef5-94b7-b3432a38a077`
- 用户意图：给 **顾老师** 发消息
- 实际行为：全局搜索后直接选“综合结果第一条”，进入群聊 **2026毕业设计**，随后标题校验失败并中止。

#### 根因（以证据为准）

旧全局搜索兜底策略为“纯键盘：输入 → Return 选第一条”，没有切换到“联系人”筛选，导致当综合结果第一条是群聊时会走错：

- ui_debug 中 `globalSearchKeyboard[*].verified=false`；`headerPreview` 仅出现 `2026毕业设计`，未出现 `顾老师`
- 截图 `wecom_global_search_verify_0/1/2` 均显示当前会话为群聊

#### 修复方案（ROI + 弹窗窗口截取 + 重试）

目标：在全局搜索弹窗中 **先点击“联系人”tab**，再在结果列表中点击目标联系人，确保不会误选群聊。

1) **新增“弹窗窗口截取”能力**

- 新增 `MacOSUIAutomation.screenshot_window_child_in_parent(...)`：
  - 从 Quartz 窗口列表中挑选“位于主窗口 bounds 内、面积较小”的子窗口/弹窗；
  - 用 `screencapture -l <wid>` 直接截取弹窗窗口，便于 OCR tabs 与结果列表。

2) **全局搜索兜底改造**

- 在输入联系人后：
  - 截取弹窗 → 在严格 ROI（仅 tabs 行）内 OCR 并点击 `联系人`
  - 再截取弹窗 → 在结果 ROI 内 OCR，选择最匹配联系人并点击
- 若切到联系人 tab 后短时未出结果：清空搜索框内容，重试 1–2 次。
- 若弹窗窗口无法截取：才回退到旧键盘 Return 策略；否则不冒险点综合结果第一条（保持防误发护栏）。

3) **ROI 约束（避免结果区“联系人”干扰）**

- 新增环境变量可覆盖：
  - `WECOM_POPUP_TABS_ROI`：默认只覆盖 tabs 行（高度很窄）
  - `WECOM_POPUP_RESULTS_ROI`：默认覆盖结果列表区域

#### 代码与测试

- 修改：
  - `backend_py/services/macos_ui_automation.py`
  - `backend_py/services/wecom_ui_controller.py`
- 新增回归测试：
  - `test_scripts/test_macos_ui_automation_popup_pick.py`

测试结果：`2 passed`

### 2026-03-09（补充修复 — 联系人结果含“@组织”导致匹配阈值误判）

#### 现象

- requestId：`75b0cdf0-0f5e-4af6-9a8d-3eb8b227cdc6`
- 已能在全局搜索弹窗内点击“联系人”tab，且 results OCR 里出现多次 `罗晨曦@深圳大学`
- 但流程一直重试，不点击任何结果，最终仍进入群聊并触发护栏中止

#### 根因（脚本证据）

旧匹配策略使用 `SequenceMatcher(target, candidate)` 直接比对整串文本，目标是 `罗晨曦`，候选是 `罗晨曦@深圳大学`：

- `SequenceMatcher("罗晨曦", "罗晨曦@深圳大学").ratio() = 0.545...`
- 小于点击阈值 `0.78`，因此即使结果存在也会被判定为“不命中”，导致不点击并反复重试。

该数值与 ui_debug 中 `resultPick.similarity=0.545...` 一致，形成闭环证据。

#### 修复（方案 A + 可选方案 B 兜底）

1) **方案 A（主修）**：匹配支持“联系人名 + 后缀信息”

- 为候选文本生成变体（`@`、括号等分隔符前的主名字）；
- 若目标是候选的子串（如 `罗晨曦` in `罗晨曦@深圳大学`）且目标长度≥2，则视为强命中（similarity=1.0），避免长度惩罚。

2) **方案 B（兜底）**：当结果区确实有 OCR 文本但连续匹配失败时，点击结果区“第一条”

- 选择 results ROI 内 y 最小的“非噪声文本框”近似第一条结果并点击；
- 点击后仍依赖主窗口 `chat_header` 护栏确认是否进入目标会话，否则继续重试（避免误发）。

#### 代码与测试

- 修改：`backend_py/services/wecom_ui_controller.py`
- 更新回归测试：`test_scripts/test_wecom_ui_controller_pick_and_noise.py`
  - 新增用例：`罗晨曦` 应能命中 `罗晨曦@深圳大学`（similarity ≥ 0.78）

测试结果：`4 passed`

### 2026-03-09（补充修复 — 联系人结果被 ROI 裁掉 + 重试重复点 tab 导致结果消失）

#### 现象

- requestId：`827e8e43-265b-4fde-8e75-e5e1c6d4cd5d`
- 用户意图：给 **顾老师** 发消息
- 实际观察：能进入全局搜索弹窗并点击“联系人”，重试后出现结果，但再次点击“联系人”后结果消失。

#### 根因（以 ui_debug 证据为准）

1) **results ROI 纵向起点过低，漏掉第一条结果卡片**

- 弹窗截图中肉眼可见结果列表里存在“顾老师”卡片；
- 但 `globalSearchPopup[*].tries[*].resultsPreview` 大量情况下只出现“没有找到相关结果 / 智能搜索”提示文案，未出现“顾老师”，
  说明 OCR 的 results ROI 没覆盖到结果卡片区域（被裁掉），导致逻辑误判为“无结果”并进入重试。

2) **重试阶段重复点击“联系人”tab，触发企微重新过滤/刷新**

- 当前实现每轮 `popup_try` 都会执行一次“tabs OCR → 点击联系人”；
- 在重试阶段再次点击 tab，可能触发 UI 刷新，导致刚出现的结果闪退/消失（用户观测一致）。

#### 修复（A+B+C+D）

- **A：调整默认 `WECOM_POPUP_RESULTS_ROI`** 上移 y 起点，确保覆盖第一条联系人结果卡片；
  同时略收紧 `WECOM_POPUP_TABS_ROI`，避免 ROI 重叠与 tabs 文本干扰。
- **B：重试不重复点击“联系人”tab**：单个 attempt 内只在首次进入弹窗时点击一次联系人；后续重试仅清空/重输关键字。
- **C：结果稳定轮询**：点击联系人或输入关键字后，按短 backoff 轮询抓取 results 截图并 OCR，避免过早判定“无结果”。
- **D：空态文案过滤**：将“没有找到相关结果/智能搜索提示”加入噪声过滤，并禁止兜底点击这些提示文案。

#### 代码与测试

- 修改：`backend_py/services/wecom_ui_controller.py`
- 更新回归测试：`test_scripts/test_wecom_ui_controller_pick_and_noise.py`
  - 增加用例：空态文案（没有找到相关结果/智能搜索）应被视为噪声，不参与点击

测试结果：企微相关单测集合 `8 passed`

