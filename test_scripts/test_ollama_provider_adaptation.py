"""Ollama provider 适配回归测试。

验证目标：
1. `OLLAMA_BASE_URL` 即便带 `/v1`，也能被归一化为原生 `/api/chat` 根地址。
2. `LLMService` 在 `LLM_PROVIDER=ollama` 时会走原生 `OllamaClient.chat()`，
   并正确透传 tools / format。
3. `ConversationController` 的网络 tool-loop 在 Ollama provider 下会用
   `role=tool + tool_name` 回填工具结果，而不是 OpenAI 风格的 `tool_call_id`。
"""

from __future__ import annotations

import json
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
async def test_ollama_client_streaming_accumulates_chunks_and_uses_timeout(monkeypatch: Any) -> None:
    import backend_py.services.ollama_client as ollama_mod
    from backend_py.services.ollama_client import OllamaClient

    captured: Dict[str, Any] = {}

    class _FakeStreamResponse:
        status_code = 200

        async def __aenter__(self) -> "_FakeStreamResponse":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

        async def aread(self) -> bytes:
            return b""

        async def aiter_lines(self):
            lines = [
                json.dumps({"model": "qwen3.5:9b", "message": {"thinking": "先", "content": ""}, "done": False}, ensure_ascii=False),
                json.dumps({"model": "qwen3.5:9b", "message": {"thinking": "想", "content": "好"}, "done": False}, ensure_ascii=False),
                json.dumps(
                    {
                        "model": "qwen3.5:9b",
                        "message": {
                            "content": "的",
                            "tool_calls": [{"function": {"name": "get_current_time", "arguments": {"tz": "Asia/Shanghai"}}}],
                        },
                        "done": False,
                    },
                    ensure_ascii=False,
                ),
                json.dumps({"model": "qwen3.5:9b", "done": True, "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 5}, ensure_ascii=False),
            ]
            for line in lines:
                yield line

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

        def stream(self, method: str, url: str, json: Dict[str, Any]) -> _FakeStreamResponse:
            captured["method"] = method
            captured["url"] = url
            captured["payload"] = json
            return _FakeStreamResponse()

    monkeypatch.setattr(ollama_mod.httpx, "AsyncClient", _FakeAsyncClient)

    client = OllamaClient(base_url="http://127.0.0.1:11434/v1", timeout_sec=123)
    result = await client.chat(
        model="qwen3.5:9b",
        messages=[{"role": "user", "content": "现在几点"}],
        stream=True,
    )

    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["payload"]["stream"] is True
    assert captured["timeout"].read == 123
    assert result.stream is True
    assert result.thinking == "先想"
    assert result.content == "好的"
    assert result.tool_calls[0]["function"]["name"] == "get_current_time"
    assert result.tool_calls[0]["function"]["arguments"] == '{"tz": "Asia/Shanghai"}'
    assert result.done is True
    assert result.done_reason == "stop"
    assert result.duration_ms is not None


@pytest.mark.anyio
async def test_llm_service_ollama_uses_native_chat_and_json_format(monkeypatch: Any) -> None:
    from backend_py.config import settings
    from backend_py.services.llm_service import LLMService
    from backend_py.services.ollama_client import OllamaChatResult

    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(settings, "ollama_model", "qwen3.5:9b")
    monkeypatch.setattr(settings, "ollama_timeout_sec", 180.0)

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
            thinking="先思考一下",
            stream=True,
            first_chunk_ms=45,
            duration_ms=320,
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
    assert captured["stream"] is True
    assert captured["think"] is False
    assert response["provider"] == "ollama"
    assert response["thinking"] == "先思考一下"
    assert response["toolCalls"][0]["function"]["arguments"] == "{}"
    assert response["usage"]["total_tokens"] == 20
    assert response["providerMeta"]["stream"] is True
    assert response["providerMeta"]["timeoutSec"] == 180.0


@pytest.mark.anyio
async def test_llm_service_ollama_rehydrates_assistant_tool_calls_for_tool_loop(monkeypatch: Any) -> None:
    from backend_py.config import settings
    from backend_py.services.llm_service import LLMService
    from backend_py.services.ollama_client import OllamaChatResult

    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:11434")
    monkeypatch.setattr(settings, "ollama_model", "qwen3.5:9b")

    service = LLMService()
    captured: Dict[str, Any] = {}

    async def _fake_chat(**kwargs: Any) -> OllamaChatResult:
        captured.update(kwargs)
        return OllamaChatResult(
            content='{"ok": true}',
            tool_calls=[],
            model="ollama-test-model",
            done=True,
            done_reason="stop",
            raw={"prompt_eval_count": 12, "eval_count": 8},
            thinking="",
            stream=True,
            first_chunk_ms=45,
            duration_ms=320,
        )

    assert service.ollama_client is not None
    monkeypatch.setattr(service.ollama_client, "chat", _fake_chat)

    await service.invoke_llm(
        [
            {"role": "user", "content": "今天天气怎么样"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_weather_1",
                        "type": "function",
                        "function": {
                            "name": "get_weather_now",
                            "arguments": '{"location": "113.99,22.67"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_name": "get_weather_now",
                "content": '{"code":"200","location":"113.99,22.67"}',
            },
        ],
        tools=[
            {
                "name": "get_weather_now",
                "description": "获取当前天气",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
        output_mode="json",
    )

    assistant_message = captured["messages"][2]
    tool_call = assistant_message["tool_calls"][0]
    assert isinstance(tool_call["function"]["arguments"], dict)
    assert tool_call["function"]["arguments"]["location"] == "113.99,22.67"


def test_llm_service_ollama_compact_prompt_used_for_schema_mode(monkeypatch: Any) -> None:
    from backend_py.config import settings
    from backend_py.services.llm_service import LLMService

    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:11434")
    monkeypatch.setattr(settings, "ollama_model", "qwen3.5:9b")

    service = LLMService()
    prompt = service._build_system_content(memory_context="", output_mode="schema_json")
    assert "response schema" in prompt
    assert "INTENT_JSON" not in prompt


@pytest.mark.anyio
async def test_llm_service_ollama_text_mode_uses_structured_route_and_predict_tier(monkeypatch: Any) -> None:
    from backend_py.config import settings
    from backend_py.services.llm_service import LLMService
    from backend_py.services.ollama_client import OllamaChatResult

    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:11434")
    monkeypatch.setattr(settings, "ollama_model", "qwen3.5:9b")
    monkeypatch.setattr(settings, "ollama_timeout_sec", 180.0)

    service = LLMService()
    captured: Dict[str, Any] = {}

    async def _fake_chat(**kwargs: Any) -> OllamaChatResult:
        captured.update(kwargs)
        return OllamaChatResult(
            content=json.dumps(
                {
                    "mode": "act",
                    "confidence": 0.96,
                    "say": "我可以用酷狗搜索并播放周杰伦的告白气球。这需要你确认一下。",
                    "actions": [
                        {
                            "name": "music_ui",
                            "arguments": {
                                "player": "kugou",
                                "action": "search",
                                "query": "周杰伦 告白气球",
                            },
                        }
                    ],
                    "reason": "用户想听指定歌曲",
                },
                ensure_ascii=False,
            ),
            tool_calls=[],
            model="ollama-test-model",
            done=True,
            done_reason="stop",
            raw={"prompt_eval_count": 20, "eval_count": 30},
            thinking="",
            stream=True,
            first_chunk_ms=120,
            duration_ms=860,
        )

    assert service.ollama_client is not None
    monkeypatch.setattr(service.ollama_client, "chat", _fake_chat)

    response = await service.invoke_llm(
        [{"role": "user", "content": "我想听周杰伦的告白气球"}],
        tools=[
            {
                "name": "music_ui",
                "description": "通过 UI 自动化控制音乐播放器",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
        max_tokens=512,
    )

    assert isinstance(captured["response_format"], dict)
    assert captured["options"]["num_predict"] == 128
    assert captured["messages"][0]["role"] == "system"
    assert captured["think"] is False
    assert "response schema" in captured["messages"][0]["content"]
    assert response["text"].startswith("INTENT_JSON:")
    assert "music_ui" in response["text"]
    assert response["providerMeta"]["structuredRoute"] is True
    assert response["providerMeta"]["structuredRouteParsed"] is True


@pytest.mark.anyio
async def test_llm_service_ollama_text_mode_falls_back_to_tool_calls_when_schema_text_empty(monkeypatch: Any) -> None:
    from backend_py.config import settings
    from backend_py.services.llm_service import LLMService
    from backend_py.services.ollama_client import OllamaChatResult

    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:11434")
    monkeypatch.setattr(settings, "ollama_model", "qwen3.5:9b")

    service = LLMService()

    async def _fake_chat(**kwargs: Any) -> OllamaChatResult:
        return OllamaChatResult(
            content="",
            tool_calls=[
                {
                    "id": "tool_1",
                    "type": "function",
                    "function": {
                        "name": "music_ui",
                        "arguments": json.dumps(
                            {"player": "kugou", "action": "search", "query": "周杰伦 告白气球"},
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
            model="ollama-test-model",
            done=True,
            done_reason="stop",
            raw={"prompt_eval_count": 12, "eval_count": 8},
            thinking="",
            stream=True,
            first_chunk_ms=40,
            duration_ms=300,
        )

    assert service.ollama_client is not None
    monkeypatch.setattr(service.ollama_client, "chat", _fake_chat)

    response = await service.invoke_llm(
        [{"role": "user", "content": "我想听周杰伦的告白气球"}],
        tools=[
            {
                "name": "music_ui",
                "description": "通过 UI 自动化控制音乐播放器",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
    )

    assert response["text"].startswith("INTENT_JSON:")
    assert "周杰伦 告白气球" in response["text"]
    assert response["providerMeta"]["structuredRouteFallback"] == "tool_calls"


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


def test_conversation_controller_music_fallback_covers_i_want_to_listen() -> None:
    from backend_py.controllers.conversation_controller import ConversationController

    assert ConversationController._is_music_request("我想听周杰伦的告白气球") is True
    assert ConversationController._extract_music_search_query("帮我播放周杰伦的告白气球") == "周杰伦 告白气球"


def test_conversation_controller_ollama_tool_pruning_music() -> None:
    from backend_py.controllers.conversation_controller import ConversationController

    tool_defs = [
        {"name": "music_ui"},
        {"name": "play_music"},
        {"name": "stop_music"},
        {"name": "media_control"},
        {"name": "execute_workflow"},
        {"name": "write_article"},
        {"name": "web_search"},
        {"name": "get_weather_now"},
        {"name": "get_device_location"},
    ]

    pruned = ConversationController._prune_tool_defs_for_ollama(
        user_text="我想听周杰伦的告白气球",
        tool_defs=tool_defs,
        include_network=True,
        include_device_location=True,
    )

    names = sorted([str(x.get("name")) for x in pruned])
    assert names == ["execute_workflow", "media_control", "music_ui", "play_music", "stop_music"]


def test_conversation_controller_ollama_tool_pruning_weather() -> None:
    from backend_py.controllers.conversation_controller import ConversationController

    tool_defs = [
        {"name": "web_search"},
        {"name": "get_latest_news"},
        {"name": "get_weather_now"},
        {"name": "get_weather_12h"},
        {"name": "get_current_time"},
        {"name": "get_ip_location"},
        {"name": "get_device_location"},
        {"name": "music_ui"},
        {"name": "execute_workflow"},
    ]

    pruned = ConversationController._prune_tool_defs_for_ollama(
        user_text="今天天气怎么样",
        tool_defs=tool_defs,
        include_network=True,
        include_device_location=True,
    )

    names = set([str(x.get("name")) for x in pruned])
    assert "get_weather_now" in names
    assert "get_weather_12h" in names
    assert "get_device_location" in names
    assert "music_ui" not in names


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
