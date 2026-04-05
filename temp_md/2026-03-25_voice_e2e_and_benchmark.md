## 2026-03-25（语音 E2E + m4a 输入 + DashScope/Ollama 对照实验）

### 目标
为毕业设计/论文补齐两类“可复现实验数据”与证据链：
- **功能 E2E 测试记录**：语音输入 → ASR 转写 → LLM 回复（可选触发工具）→ TTS 输出。
- **性能对照数据**：ASR 延迟、LLM 响应时间（DashScope vs Ollama）、TTS 首包/总耗时。

所有产物以 **runId** 为单位落盘，满足“可审计、可复现、可对照”。

---

### 1) m4a（手机录音）输入策略
你的手机录音通常是 `m4a`（MP4 容器）。为了让测试更稳定：
- **E2E/benchmark 客户端脚本**：优先使用 macOS 自带 `afconvert` 将 `m4a` 转成 `wav` 再发送/调用（降低 ASR 端对 format 的兼容差异风险）。
- **后端 `ASRService`**：也补齐了 `m4a/mp4` 的文件头识别与 `input_audio.format` 映射，便于未来“直接发送 m4a bytes”场景。

---

### 2) 功能测试（语音 E2E）
脚本：`test_scripts/test_e2e_socketio_voice_flow.py`

#### 2.1 安全默认（不执行 UI 自动化）
- 默认 **不自动确认**高风险工具；如果 LLM 触发了 UI 自动化，会在脚本里抛错并停止，避免误操作。

示例（建议用于论文“功能正确性”记录）：
- 输入：手机录音 `m4a`
- 文本：纯对话/写作类（不触发工具）

运行：
- `backend_py/.venv/bin/python test_scripts/test_e2e_socketio_voice_flow.py --audio <你的m4a路径>`

如需对照实验（覆盖 ASR 并额外发送一次 `text-command`）：
- `backend_py/.venv/bin/python test_scripts/test_e2e_socketio_voice_flow.py --audio <你的m4a路径> --override-text "写一首五言绝句"`

#### 2.2 完整链路（含工具，高风险）
如需覆盖“语音→操作→语音”全链路（例如 `music_ui`）：
- 运行时加：`--auto-approve-tools`

运行：
- `backend_py/.venv/bin/python test_scripts/test_e2e_socketio_voice_flow.py --audio <你的m4a路径> --auto-approve-tools`

#### 2.3 输出与证据
- 脚本产物目录：`e2e_runs/<runId>/`
  - `server.log`：本次后端日志
  - `voice_flow_timeline.json`：事件时间线（ASR/LLM/TTS/确认/tool-result）
- 若触发 UI 自动化：后端证据目录：`~/Documents/VoiceAssistant/ui_debug/<requestId>/`

建议在论文中引用：
- `voice_flow_timeline.json` 的关键字段（requestId、各事件时间戳、ASR 文本、TTS 完成时间等）
- `ui_debug/<requestId>/` 的截图/JSON（证明“确实点击了/界面变化了”）

---

### 3) 性能测试（ASR/LLM/TTS 基准 + DashScope/Ollama 对照）
脚本：`test_scripts/test_voice_perf_benchmark.py`

#### 3.1 设计原则
- 直接调用 `backend_py.services.*`，避免引入 Socket.IO、UI 自动化等额外抖动。
- LLM 侧默认禁用 tools（`tools=[]`），让对照更公平、方差更小。
- 每个 provider 支持 `--repeat N`，输出 mean/p50/p90/p95/min/max。

#### 3.2 典型对照运行
- **仅对照 LLM（最推荐）**：
  - `DASHSCOPE_API_KEY=... OLLAMA_BASE_URL=http://localhost:11434/v1 OLLAMA_MODEL=qwen3.5 backend_py/.venv/bin/python test_scripts/test_voice_perf_benchmark.py --providers dashscope,ollama --repeat 5 --skip-asr --skip-tts`

- **测全套（DashScope：ASR+LLM+TTS）**：
  - `DASHSCOPE_API_KEY=... backend_py/.venv/bin/python test_scripts/test_voice_perf_benchmark.py --audio <你的m4a路径> --providers dashscope --repeat 3`

#### 3.3 输出
- 产物目录：`e2e_runs/bench_<runId>/voice_perf_benchmark.json`
- 结果结构：
  - `meta.env`：实验环境（OS、Python、CPU 架构等）
  - `providers.<provider>.summary`：统计指标
  - `providers.<provider>.runs[]`：每次重复的原始数据

建议在论文里用表格呈现：
- ASR：`asr_ms.p50 / p90`
- LLM：`llm_ms.p50 / p90`（DashScope vs Ollama）
- TTS：`tts_ttfb_ms.p50`（首包）与 `tts_total_ms.p50`

---

### 4) 论文写作落点（可直接复用）
- **功能测试章节**：
  - 用 `voice_flow_timeline.json` 展示“语音输入→系统输出”的事件链与关键时间戳。
- **性能评估章节**：
  - 用 `voice_perf_benchmark.json` 的 p50/p90 做对照表，说明 DashScope 与 Ollama 的响应时间差异。
- **可观测性与可复现性章节**：
  - 引用 `ui_debug/<requestId>/` 作为“真实执行证据链”（截图 + JSON + requestId 贯穿）。
