# 2026-02-16 KuGou VLM 全控模式落地（本地 Ollama）

## 背景

酷狗 UI 自动化当前主链路是 OCR-first（Vision OCR + ROI + 状态机），但自绘/手绘 UI 会让“找控件/读文本”不稳定。
本次落地 **完全不同的第二模式**：本地 VLM 全控模式（由 VLM 决策 click/type_text/noop），并通过配置开关与原 OCR 工作流隔离。

## 关键决策（已确认）

- **两种模式完全隔离**：
  - `ocr`：沿用现有 OCR 状态机（默认）。
  - `vlm`：完全由本地 Ollama VLM 驱动，不自动回退 OCR。
- **动作协议（升级）**：VLM 必须输出 `UI_PLAN_JSON`，一次性包含 `state + action + target + bbox_norm`，并提供 `state_confidence/action_confidence` 与 `evidence` 便于校验与排障。
- **输入策略**：每次 `type_text` 前都执行 **Cmd+A + Delete** 清空。
- **验证策略**：以 state 语义约束为主（state→target 硬校验），UI 变化验证为辅助；允许幂等步骤与 `noop`；不合法时最多触发一次纠错重问。
- **可观测性**：每一步 request/response、校验失败原因、以及输入 WebP(q=95) 都落盘到 `~/Documents/VoiceAssistant/ui_debug/<requestId>/`。

## 本次实现摘要

- 更新 `backend_py/services/vlm_ui_driver.py`：
  - 截图（PNG）→ 转 WebP（quality=95，WebP only）→ base64 → Ollama `/api/chat`。
  - 解析 `UI_PLAN_JSON`（state+action+target+bbox）并做硬校验，不合法时最多纠错重问一次。
  - 每步落盘：request/response、校验失败原因、输入 WebP 与 step 执行证据。
- 在 `music_ui(player="kugou", action="search")` 处接入分流：
  - `VOICE_ASSISTANT_UI_AUTOMATION_MODE=vlm` 时走 VLM driver。
  - 其他情况保持走 OCR 工作流。
- 新增测试脚本：
  - `test_scripts/test_ollama_chat_smoke.py`
  - `test_scripts/debug_kugou_vlm_driver_dry_run.py`

## 后续待验证点

- VLM prompt 的 state 识别与 done 停机条件，需要基于真实截图迭代。
- 性能目标（5-10s）是否满足：建议在常用分辨率下用 dry-run 先评估 VLM 延迟。
