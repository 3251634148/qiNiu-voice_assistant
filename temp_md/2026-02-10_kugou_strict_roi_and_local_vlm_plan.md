## 2026-02-10 - KuGou 严格 ROI 修复 + 本地 VLM 方案评估

### 背景
近期 KuGou UI 自动化链路在“进入指定子页/定位顶部 tabs/进入搜索”步骤不稳定。典型失败为：OCR 能识别到目标词，但由于 ROI 误采样/裁剪不当，导致关键 tabs 未被截入或被裁断，从而误判状态并触发错误点击。

同时讨论了一个中期演进方向：引入本地视觉大模型（VLM）做 UI 自动化的“失败兜底/建议器”，以应对手绘 UI、低对比度文本等 OCR 失效场景。

---

### 本次代码变更（已落地）

#### 1) “我的页顶部 tabs（音乐/艺人/动态）”严格 ROI
- 新增 `KUGOU_ROIS["my_top_tabs_strict"]`，并在 `favorites_first_v2` 路径使用该 ROI。
- 引入严格校验：在该 ROI 内（按 `min_confidence>=0.6` 过滤）必须且只能识别到 `音乐/艺人/动态`，否则直接失败并通过 `ui_debug/<requestId>/` 落盘证据（截图 + OCR boxes + ROI crop）。

#### 2) `music_ui(search)` 新增“推荐”复位步骤
- 新增 `KUGOU_ROIS["music_recommend_tabs_strict"]`（默认值为初始猜测，后续可用脚本扫参校准）。
- 在 `music_main_ready` 状态下，第一次进入时强制对“推荐”执行两次点击，并在点击前后做严格校验：必须且只能识别到 `推荐/频道/歌单/歌手`。

#### 3) 新增严格 ROI 扫参脚本
- 新增 `test_scripts/debug_kugou_strict_tabs_roi_calib.py`
  - 支持 `--mode my_tabs` 与 `--mode recommend_tabs`
  - 支持离线图片 `--image` 或现场抓窗 `--capture`
  - 输出 Top-N 候选 ROI 的 crop PNG 与 JSON 评分结果到 `ui_debug/<runId>/`

---

### 本地 VLM（Ollama）接入评估（建议）

#### 定位
- 不建议短期用 VLM 替代 OCR-first 主链路。
- 建议作为“失败兜底/建议器”模块：当 OCR/模板/规则无法定位时，裁剪 ROI 并调用本地 VLM 返回候选点击区域/坐标，再由本地状态机验证。

#### 风险控制
- 成本与延迟：仅在失败时调用；只传 ROI（非整屏）；启用 `keep_alive` 让模型常驻。
- 安全：高风险动作仍需确认；每步点击必须有“状态验证”与证据链落盘。

---

### 修改文件清单
- `backend_py/services/music_controller.py`
- `test_scripts/debug_kugou_strict_tabs_roi_calib.py`
- `docs/CHANGELOG.md`
- `temp_md/2026-02-10_kugou_strict_roi_and_local_vlm_plan.md`
