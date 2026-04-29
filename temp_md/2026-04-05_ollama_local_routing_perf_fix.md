# 2026-04-05 Ollama 本地路由性能修复记录

## 背景

问题现象：
- `LLM_PROVIDER=ollama` 时，本地 `qwen3.5:9b` 在短意图请求上响应明显偏慢。
- 典型请求“我想听周杰伦的告白气球”曾出现长时间等待后仍未正确进入音乐确认执行链。

## 根因摘要

1. 本地主链沿用了远程模型场景下的重 system prompt 与工具协议，导致本地 9B 模型在短任务上也承担了较重的路由负担。
2. Ollama 主链默认允许较大的 `num_predict`，短任务存在过度生成开销。
3. 当本地模型未稳定输出结构化文本时，音乐类兜底识别对“我想听 + 歌名”覆盖不足。

## 本次修复

### 1. Ollama 紧凑版 prompt
- 在 `backend_py/services/llm_service.py` 中新增 `self.ollama_compact_system_prompt`。
- 仅在 Ollama 的结构化路由场景下启用紧凑提示词，减少无关规则负担。

### 2. Ollama 分档生成预算
- 新增 `_build_ollama_generation_options()`。
- 按任务类型与输出模式选择 `num_predict`：
  - 结构化/JSON 路由：优先使用较小预算。
  - 长文生成：保留更高预算。
- 结构化/JSON 路由关闭 `think`，降低本地推理耗时与推理链冗余输出。

### 3. Ollama 结构化 JSON 路由
- 在 `invoke_llm()` 中，Ollama 的默认 `text` 路径改为内部走 `schema_json`。
- 新增：
  - `_build_ollama_route_schema()`
  - `_normalize_ollama_route_payload()`
  - `_adapt_ollama_structured_route_result()`
- 结构化结果会被适配回现有主链兼容的 `INTENT_JSON + SAY` 文本协议。
- 当模型返回空文本但给出 `tool_calls` 时，新增 tool-call fallback，自动补出兼容文本，避免再次落回“未开始播放”类错误文案。

### 4. 音乐兜底识别增强
- 在 `backend_py/controllers/conversation_controller.py` 中补齐：
  - `_is_music_request()` 对 `我想听`、`我要听`、`帮我播放`、`给我播放`、`帮我放`、`给我放`、`听一下` 等表达的识别。
  - `_extract_music_search_query()` 对 `帮我播放/请播放/麻烦播放/给我播放` 等前缀的清洗。

### 5. A3：Ollama 工具按意图裁剪
- 在 `backend_py/controllers/conversation_controller.py` 中新增 `_prune_tool_defs_for_ollama()`：
  - 仅对 `LLM_PROVIDER=ollama` 生效。
  - 根据用户文本意图，裁剪传给本地模型的 `tools` schema（例如音乐只保留 `music_ui/play_music/stop_music/media_control/execute_workflow`）。
  - 对天气/新闻/时间等实时信息仅在需要时开放联网工具。

### 6. Ollama tool loop 协议适配修复
- 在 `backend_py/services/llm_service.py` 中新增 `_prepare_ollama_messages()`。
- 作用：当主链进入 Ollama 的多轮 tool loop 时，把内部统一格式中的 `assistant.tool_calls[].function.arguments=<JSON字符串>` 反向还原为 Ollama 原生所需的对象结构。
- 这样可以避免第二轮 `/api/chat` 因 assistant tool call 回填格式不合法而触发 `400 Bad Request`。

## 验证结果

### 自动化测试
执行：

```bash
backend_py/.venv/bin/python -m pytest test_scripts/test_ollama_provider_adaptation.py -q
```

结果：
- 初始修复阶段：`9 passed`
- 加入 A3 与 tool loop 协议修复后：`12 passed`

### 本地单次验证
请求：`我想听周杰伦的告白气球`

修复后观察到：
- 能稳定返回兼容主链的 `INTENT_JSON + SAY`
- `thinkingChars` 降为 `0`
- `providerMeta.structuredRouteParsed = true`
- 实测样本 `durationMs` 降至约 `24942ms`

### tool loop 复现验证
- 使用模拟的天气 tool-loop 消息（assistant tool_calls + tool result）直接调用 `LLMService.invoke_llm()`。
- 修复后不再出现第二轮 `400 Bad Request`。
- 实测可正常返回天气总结文本。

## 修改文件

- `backend_py/services/llm_service.py`
- `backend_py/services/ollama_client.py`
- `backend_py/controllers/conversation_controller.py`
- `test_scripts/test_ollama_provider_adaptation.py`

## 说明

本次改动遵循最小化修改原则：
- 未修改 DashScope 主链。
- 未修改前端协议。
- 未调整 ToolRouter 业务执行逻辑。
- 重点聚焦 Ollama 本地意图路由性能与音乐确认链稳定性。
