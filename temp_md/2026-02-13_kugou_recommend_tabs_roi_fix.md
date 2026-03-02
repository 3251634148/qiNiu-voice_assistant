## 2026-02-13 - KuGou 推荐 tabs 严格 ROI 误判修复（A+B+C）

### 背景与现象
- 测试指令："我现在心情很好，想听歌。"
- 计划动作：`music_ui(player="kugou", action="search", query="周杰伦 阳光彩虹小白马")`
- 前端报错摘要：严格推荐 tabs ROI 校验失败（missing 全部、extra 反而包含内容区卡片标题）。
- requestId：`f25ea9eb-955a-4d60-b469-87f205b15cd8`
- 调试产物目录：`/Users/westar/Documents/VoiceAssistant/ui_debug/f25ea9eb-955a-4d60-b469-87f205b15cd8/`

### 复现步骤（可选）
1. 保持酷狗窗口存在并可被截图（已授权辅助功能/Vision OCR）。
2. 运行 E2E 或在前端发起同类请求，确保生成 requestId 并落盘产物。

### 根因分析（证据链）
- 失败发生在 `backend_py/services/music_controller.py` 的 KuGou OCR 工作流 `_force_recommend_tab()`。
- 该逻辑在 `state=music_main_ready` 时会尝试“强制双击推荐”以收敛到推荐子页。
- 旧 ROI：`KUGOU_ROIS["music_recommend_tabs_strict"] = (0.18, 0.12, 0.56, 0.08)`
  - 该 ROI 在当前酷狗 UI/窗口尺寸下偏移过低，裁剪到了内容区卡片文本（如“排行榜/每日推荐/百万收藏”），导致严格校验 `missing=[推荐,频道,歌单,歌手]`。

### 离线验证（扫参与回归）
- 使用失败截图 `kugou_flow_state_0_1770965934351.png` 做离线扫参，得到更稳 ROI：
  - 推荐使用：`(0.10, 0.03, 0.70, 0.06)`
- 扫参产物（本地示例 run）：
  - `ui_debug/roi_calib_recommend_yfix_*/strict_tabs_roi_calib_recommend_tabs_*.json`
  - `ui_debug/roi_calib_recommend_yfix_*/strict_tabs_roi_top_recommend_tabs_0_*.png`

### 本次代码修复点（A+B+C）
1. **A：ROI 修正**
   - 将 `music_recommend_tabs_strict` 的默认 ROI 调整为 `(0.10, 0.03, 0.70, 0.06)`。

2. **B：best-effort（不阻断主流程）**
   - `_force_recommend_tab()` 不再“严格校验失败就 raise 终止”；失败会写入 `debug_info["warnings"]` 并继续后续“进入搜索页”逻辑。
   - 增加 ROI 兜底：先尝试新 ROI，再尝试 legacy ROI。

3. **C：可配置化**
   - 支持环境变量覆盖：
     - `KUGOU_RECOMMEND_TABS_STRICT_ROI="0.10,0.03,0.70,0.06"`
   - 便于主题/版本变更后无需改代码快速恢复。

### 回归脚本
新增离线回归脚本：`test_scripts/test_kugou_recommend_tabs_roi_regression.py`

- 运行示例（对某张截图验证推荐 tabs ROI）：
  - `VOICE_ASSISTANT_DEBUG_RUN=kugou_roi_reg_$(date +%s) backend_py/.venv/bin/python test_scripts/test_kugou_recommend_tabs_roi_regression.py --image /path/to/kugou.png`

- 临时覆盖 ROI（脚本内部设置 env 并 reload 模块）：
  - `VOICE_ASSISTANT_DEBUG_RUN=kugou_roi_reg_$(date +%s) backend_py/.venv/bin/python test_scripts/test_kugou_recommend_tabs_roi_regression.py --image /path/to/kugou.png --env-roi "0.10,0.03,0.70,0.06"`

### 性能问题（第二阶段）

#### 现象
- 你反馈“现在能稳定搜索播放歌曲了，但进入酷狗后开始等待时间很久”。
- 典型慢点：
  - 进入酷狗后到能点到侧边栏“音乐”耗时约 15s
  - 从音乐主页进入搜索入口耗时约 8s
- requestId：`1a7d399d-5987-4dcd-a81d-a486b799427e`

#### 根因方向（以代码路径为准）
- `normalize_process_window()`：AppleScript 对窗口 `size/position` 设置在部分机器/系统版本上会明显偏慢。
- `screenshot_window()`：每次都会 `screencapture`，且默认执行 PNG 无损重压缩（像素不变，但 CPU 会有额外开销）。
- OCR：为了稳态识别，主链路会多次对不同 ROI 做高倍率 OCR（3.2x 这类 scale）。

#### 已落地的优化（仅方案1/2）
- **方案1（跳过重复归一化）**：若 `kugou_flow_init` 的 `windowBounds` 已满足目标尺寸/位置（允许 ±2px），则跳过 `normalize_process_window()`。
- **方案2（复用截图减少重复 screencapture）**：在点击侧边栏“音乐”时，允许复用刚截图的 `kugou_window_normalized` 作为 OCR 输入，减少一次 `screencapture`。

### 耗时阶段基准/回归脚本
新增脚本：`test_scripts/test_kugou_search_latency_benchmark.py`

#### 目的
- 把关键阶段的耗时拆出来落盘：置前、截图（含 PNG 重压缩）、窗口归一化、OCR 等。
- 产物目录：`~/Documents/VoiceAssistant/ui_debug/<VOICE_ASSISTANT_DEBUG_RUN>/`

#### 用法
- 仅测量（默认不点击，安全）：
  - `VOICE_ASSISTANT_DEBUG_RUN=kugou_latency_$(date +%s) backend_py/.venv/bin/python test_scripts/test_kugou_search_latency_benchmark.py`

- 强制窗口归一化（用于评估 `normalize_process_window()` 的真实耗时）：
  - `VOICE_ASSISTANT_DEBUG_RUN=kugou_latency_$(date +%s) backend_py/.venv/bin/python test_scripts/test_kugou_search_latency_benchmark.py --normalize-mode always`

- 禁用 PNG 无损重压缩（用于评估压缩开销）：
  - `VOICE_ASSISTANT_DEBUG_RUN=kugou_latency_$(date +%s) backend_py/.venv/bin/python test_scripts/test_kugou_search_latency_benchmark.py --disable-png-recompress`

- 可选：计时真实点击（高风险，谨慎用）：
  - `VOICE_ASSISTANT_DEBUG_RUN=kugou_latency_$(date +%s) backend_py/.venv/bin/python test_scripts/test_kugou_search_latency_benchmark.py --do-click-music --do-click-enter-search`

#### 如何看结果
- 关注输出 JSON 中：
  - `totalMs`：本次总耗时
  - `stages[*].durationMs`：每个阶段耗时（例如 `normalize_window`、`screenshot_init`、`ocr_sidebar_music`）
  - `stages[*].meta.capture.pngLosslessCompress`：截图阶段是否执行了 PNG 无损重压缩及其元信息

### 后续建议（可选）
- 如果未来酷狗 UI 再次漂移：
  - 优先跑 `test_scripts/debug_kugou_strict_tabs_roi_calib.py --mode recommend_tabs` 在最新截图上扫参；
  - 把新的 ROI 先用 `KUGOU_RECOMMEND_TABS_STRICT_ROI` 覆盖验证通过，再决定是否固化到代码默认值。

---

## 2026-02-13 - KuGou 搜索入口误判与“加载中”截图过早（方案1+2）

### 背景与现象
- requestId：`5035e8ee-85eb-4d31-87e8-4a5286e4d336`
- 现象1：实际上已经进入搜索界面，但仍发生多轮“搜索入口”点击尝试。
- 现象2：点击“单曲”后马上 OCR，结果页仍在“加载中，请稍候”，导致目标歌未识别而播放失败。

### 证据链（关键调试产物）
- `kugou_ocr_workflow_debug_state_3_1770985943380.json`
  - `searchEntryVerify[*].preview` 多次出现 `"5史搜索"`，但 `ok=false` 导致继续重试点击。
- `kugou_song_list_ocr_boxes_target_song_not_found_1770985950768.json`
  - `previewTop12=['加载中，请稍候']`，说明 OCR 发生在列表仍处于加载态。

### 根因定位（代码层）
- **搜索入口误判**：
  - `_verify_search_view()` 对“进入搜索态”的判定过于依赖 `取消/历史搜索`。
  - 当 OCR 把“历史搜索”误识别成 `"5史搜索"`（缺失“历”字）时，判定失败，触发多轮点击重试。
- **加载态未等待**：
  - 点击结果页 `单曲` 后仅固定 `sleep(0.35)` 就开始 `kugou_song_list` ROI OCR。
  - 在慢机器/网络下，列表区域会出现“加载中，请稍候”，导致被当作最终列表页并报“找不到歌”。

### 本次修复点（方案1+2）
- **A1（按你的补充）**：在 `top_search_verify` ROI 内，只要出现 `搜索` 或 `搜` 或 `索` 任一，即可认为已进入搜索输入态。
- **方案2（焦点信号兜底）**：当 OCR 不稳定时，增加 `get_focused_ui_element_info()` 判断；若焦点角色为 `Text/Field` 且顶部 ROI 出现 `搜索/搜/索`，直接视为 `search_view`，避免重复点击。
- **方案1（加载态等待）**：在单曲列表 OCR 前增加短轮询：若列表 ROI 中识别到“加载中/请稍候/稍候”，则等待并重试，直到加载结束或超时。
