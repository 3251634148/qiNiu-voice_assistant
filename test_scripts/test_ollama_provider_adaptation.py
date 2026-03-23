"""Ollama provider 适配回归测试。

验证目标：
1. `OLLAMA_BASE_URL` 即便带 `/v1`，也能被归一化为原生 `/api/chat` 根地址。
2. `LLMService` 在 `LLM_PROVIDER=ollama` 时会走原生 `OllamaClient.chat()`，
   并正确透传 tools / format。
3. `ConversationController` 的网络 tool-loop 在 Ollama provider 下会用
   `role=tool + tool_name` 回填工具结果，而不是 OpenAI 风格的 `tool_call_id`。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class _FakeNetToolResult:
    def __init__(self, *, content_for_model: str, debug_payload: Dict[str, Any]) -> None:
        self.content_for_model = content_for_model
        self.debug_payload = debug_payload


def test_ollama_client_normalizes_root_base_url() -> None:
    from backend_py.services.ollama_client import OllamaClient

    assert OllamaClient._normalize_root_base_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434"
    assert OllamaClient._normalize_root_base_url("http://127.0.0.1:11434/") == "http://127.0.0.1:11434"
    assert OllamaClient._normalize_root_base_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"
    assert OllamaClient._normalize_root_base_url("http://127.0.0.1:11434/api") == "http://127.0.0.1:11434"
    assert OllamaClient._normalize_root_base_url("http://127.0.0.1:11434/api/chat") == "http://127.0.0.1:11434"


@pytest.mark.anyio
async def test_llm_service_ollama_uses_native_chat_and_json_format(monkeypatch: Any) -> None:
    from backend_py.config import settings
    from backend_py.services.llm_service import LLMService
    from backend_py.services.ollama_client import OllamaChatResult

    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(settings, "ollama_model", "qwen3.5:9b")

    service = LLMService()
    captured: Dict[str, Any] = {}

    async def _fake_chat(**kwargs: Any) -> OllamaChatResult:
        captured.update(kwargs)
        return OllamaChatResult(
            content='{"ok": true}',
            tool_calls=[
                {
                    "id": "tool_1",
                    "type": "function",
                    "function": {"name": "get_current_time", "arguments": "{}"},
                }
            ],
            model="ollama-test-model",
            done=True,
            done_reason="stop",
            raw={"prompt_eval_count": 12, "eval_count": 8},
        )

    assert service.ollama_client is not None
    monkeypatch.setattr(service.ollama_client, "chat", _fake_chat)

    response = await service.invoke_llm(
        [{"role": "user", "content": "现在几点"}],
        tools=[
            {
                "name": "get_current_time",
                "description": "获取当前时间",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
        output_mode="json",
    )

    assert captured["response_format"] == "json"
    assert captured["messages"][0]["role"] == "system"
    assert captured["tools"][0]["function"]["name"] == "get_current_time"
    assert response["provider"] == "ollama"
    assert response["toolCalls"][0]["function"]["arguments"] == "{}"
    assert response["usage"]["total_tokens"] == 20


@pytest.mark.anyio
async def test_llm_service_schema_json_requires_schema(monkeypatch: Any) -> None:
    from backend_py.config import settings
    from backend_py.services.llm_service import LLMService

    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:11434")
    monkeypatch.setattr(settings, "ollama_model", "qwen3.5:9b")

    service = LLMService()

    with pytest.raises(ValueError, match="response_schema"):
        await service.invoke_llm(
            [{"role": "user", "content": "返回 JSON"}],
            output_mode="schema_json",
        )


@pytest.mark.anyio
async def test_ollama_tool_loop_uses_tool_name_message(monkeypatch: Any) -> None:
    import socketio

    from backend_py.config import settings
    from backend_py.controllers.conversation_controller import ConversationController

    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:11434")
    monkeypatch.setattr(settings, "ollama_model", "qwen3.5:9b")

    sio = socketio.AsyncServer(async_mode="asgi")
    controller = ConversationController(sio=sio)

    captured_calls: List[Dict[str, Any]] = []

    async def _fake_invoke_llm(messages: List[Dict[str, Any]], **kwargs: Any) -> Dict[str, Any]:
        captured_calls.append({"messages": messages, "kwargs": kwargs})
        return {"text": "SAY: 好的", "toolCalls": []}

    async def _fake_execute(*, name: str, arguments: Dict[str, Any], request_id: str) -> _FakeNetToolResult:
        return _FakeNetToolResult(
            content_for_model='{"code":"200","now":"10:00"}',
            debug_payload={"ok": True, "tool": name, "args": arguments},
        )

    monkeypatch.setattr(controller.llm_service, "invoke_llm", _fake_invoke_llm)
    monkeypatch.setattr(controller.network_tools_service, "execute", _fake_execute)
    monkeypatch.setattr(controller.network_tools_service, "dump_debug_artifact", lambda **kwargs: "/tmp/ollama_tool_loop.json")

    llm_resp = {
        "text": "",
        "toolCalls": [
            {
                "id": "time_1",
                "type": "function",
                "function": {"name": "get_current_time", "arguments": "{}"},
            }
        ],
    }

    await controller._maybe_run_network_tool_loop(
        sid="web_test_ollama",
        request_id="req_ollama_tool_loop",
        user_text="现在几点",
        messages=[],
        llm_resp=llm_resp,
        tool_defs=[],
    )

    assert captured_calls, "tool-loop 应继续调用 LLM 总结工具结果"
    tool_messages = [m for m in captured_calls[-1]["messages"] if m.get("role") == "tool"]
    assert tool_messages, "第二轮 messages 中应包含工具结果消息"
    assert tool_messages[-1]["tool_name"] == "get_current_time"
    assert "tool_call_id" not in tool_messages[-1]
