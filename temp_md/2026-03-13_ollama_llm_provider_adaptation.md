# 2026-03-13 Ollama LLM Provider 适配记录

## 目标

- 保留现有 `INTENT_JSON + SAY` 高层协议与主业务链路。
- 当 `LLM_PROVIDER=ollama` 时，不再复用 OpenAI 兼容 `/chat/completions`，改为接入 Ollama 原生 `/api/chat`。
- 对齐 Ollama 文档中的 `tool_calls`、`tool` 消息回填、`format=json/schema` 能力。

## 本次改动

### 1. `backend_py/services/llm_service.py`

- 保留 `invoke_llm()` 统一入口，不改上层调用方式。
- 内部分拆：
  - `_invoke_dashscope_chat_completions()`
  - `_invoke_ollama_api_chat()`
- 新增参数：
  - `response_schema`
  - `output_mode`
- Ollama provider 下：
  - 使用原生 `/api/chat`
  - 支持 `format="json"` 与 `format=<json schema>`
  - 把返回结构统一整理成当前控制器可消费的内部格式

### 2. `backend_py/services/ollama_client.py`

- 新增 base URL 归一化，兼容：
  - `http://host:11434`
  - `http://host:11434/v1`
  - `http://host:11434/api`
  - `http://host:11434/api/chat`
- 返回结构扩展为：
  - `content`
  - `tool_calls`
  - `model`
  - `done`
  - `done_reason`
  - `raw`
- 对 Ollama 原生 tool calling 做本地规范化：
  - `function.arguments` 对象转成 JSON 字符串
  - 缺失 tool id 时自动补本地 id

### 3. `backend_py/controllers/conversation_controller.py`

- 新增 provider 感知的 tool-loop 消息构造器：
  - `_build_assistant_tool_call_message()`
  - `_build_tool_result_message()`
- 在 Ollama provider 下，工具结果回填改为：
  - `{"role": "tool", "tool_name": "...", "content": "..."}`
- 在 DashScope 下继续保留：
  - `{"role": "tool", "tool_call_id": "...", "content": "..."}`
- 偏好抽取在 Ollama provider 下显式走 `output_mode="json"`

## 新增/更新测试

- 新增 `test_scripts/test_ollama_provider_adaptation.py`
  - 验证 `OLLAMA_BASE_URL` 归一化
  - 验证 `LLMService` 在 Ollama 下走原生 `chat()` 且透传 `format=json`
  - 验证 `schema_json` 缺少 schema 会报错
  - 验证 Ollama tool-loop 用 `tool_name` 回填工具结果
- 更新 `test_scripts/test_ollama_chat_smoke.py`
  - 输出 `normalizedBaseUrl`
  - 输出 `toolCalls`

## 已验证

执行命令：

```bash
cd backend_py
source .venv/bin/activate
python -m pytest ../test_scripts/test_ollama_provider_adaptation.py ../test_scripts/test_device_location_tool_loop_regression.py -q
```

结果：

- `7 passed`

## 当前边界

- 未改 ASR/TTS 主流程。
- 未改前端 Socket.IO 协议。
- 未改 ToolRouter 的业务执行逻辑。
- 未引入 LLM 文本流式输出；本次仅修正 provider 协议层与 tool-loop 回填层。
