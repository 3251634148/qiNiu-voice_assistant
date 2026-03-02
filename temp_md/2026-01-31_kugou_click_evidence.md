## 2026-01-31 - KuGou 点击证据增强

### 背景
- 需要在不改变业务流程的前提下，增强 UI 自动化点击的证据链。
- 目标是能复盘“系统以为点了哪里、OCR 框在哪里、点击点是否落入 OCR 框”。

### 本次改动
- 在 `backend_py/services/music_controller.py` 的 `_click_screen_point` 中补充证据输出：
  - 将屏幕坐标回映射到窗口截图坐标。
  - 输出 `ocrHitTest`（是否命中 OCR 框、与框中心偏移）。
- 补全 `backend_py/.env` 模板，覆盖 DashScope、LLM stub、调试 runId、企业微信相关配置。

### 影响范围
- 不改变点击流程与业务逻辑，仅增加调试证据输出。

### 后续建议
- 若需要完整“鼠标落点”证据，可保持 `VOICE_ASSISTANT_DEBUG_WARP=1`，以生成光标位置信息。

### 2026-01-31 - 侧边栏 ROI 收敛与点击证据
- 在 `KUGOU_ROIS` 中新增“侧边栏二次裁剪”组合 ROI：`sidebar_music_sub` + `sidebar_music`。
- `_click_sidebar_music` 优先使用收窄 ROI 识别“音乐”，失败再回退到完整侧边栏 ROI。

## 2026-02-02 - 探针闭环与截图无损压缩

### 改动点
- `test_scripts/debug_kugou_sidebar_music_click_probe.py`：调用 `click_at_debug` 时传入 `window_bounds/image_size/ocr_box`，使 clickDebug JSON 含 `targetAsImagePoint` 与 `ocrHitTest`；并对 `--cursor-shot` 产出的全屏截图记录 `pngLosslessCompress`。
- `backend_py/services/macos_ui_automation.py`：`screenshot`/`screenshot_window` 在落盘 PNG 后做一次无损重压缩（仅重 deflate IDAT，像素不变），减少磁盘占用。
- `backend_py/services/macos_ui_automation.py`：`click_at_debug` 落盘 `screenMeta`（screens frame / globalMaxY / 目标点 AppKit 与屏幕局部坐标），用于对齐企业微信截图工具的坐标系。

### 环境变量
- `VOICE_ASSISTANT_PNG_LOSSLESS_COMPRESS`
  - `1`（默认）：启用无损压缩
  - `0`/`false`/`no`：禁用（用于排查或性能对照）

### 证据链字段
- `screenshot_window(...)` 返回新增：`pngLosslessCompress`（压缩前/后大小与节省字节数）。
- `click_at_debug(...)` 落盘 JSON：`targetAsImagePoint` / `ocrHitTest` / `windowMeta`。

## 2026-02-03 - 点击落点可视化 + 企微坐标校准

### 改动点
- `backend_py/services/macos_ui_automation.py`：`click_at_debug(..., cursor_shot=True)` 将在落盘 JSON 中输出 `cursorShots`：
  - `afterWarp`：warp 光标到目标点后的全屏光标截图
  - `afterClick`：点击后的全屏光标截图
  - 并结合 `mouseAfterClick` 与 `deltaAfterClick`，形成“视觉 + 数值”的落点证据。
- `backend_py/services/macos_ui_automation.py`：`screenMeta` 输出多口径 `globalMaxY`：
  - `globalMaxYByScreens`：基于 `NSScreen.frame` 计算
  - `globalMaxYInferred`：同点双 API 采样推断（`AppKit.y + Quartz.y`）
  - `globalMaxYUsed`：实际用于换算的口径（优先 inferred）
- `backend_py/services/macos_ui_automation.py`：修复 `windowBounds.y` 的坐标口径（按 event-space top-left 解释），避免在多屏环境下把 `windowBounds` 当 bottom-left 导致点击点纵向偏移。
- `test_scripts/debug_wecom_coord_calibration.py`：新增校准脚本，把“当前光标/可选 warp 目标”在多口径下的坐标落盘到 `ui_debug/<runId>/wecom_coord_calib_*.json`。
