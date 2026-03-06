"""回归测试：天气/定位 tool-loop 使用正确 session_id，并且不会抛未捕获异常。

根因（来自真实 ui_debug 证据）：
- handle_text_command 已解析出 session_id=clientId，但调用 tool-loop 时传了原始 sid
- tool-loop 内部又用 sid 取 session，导致读到默认 device_location_enabled=False
- 结果：capability 显示开启，但 local_tool_request 记录 enabled=false，并拒绝执行定位
- 随后天气 location="" 无法 override，触发 RuntimeError（asyncio Task exception）

本文件用 mock/stub 验证修复后的行为：
1) tool-loop 必须以解析后的 session_id 读取会话态（enabled=true）
2) 当天气 location 为空且无 device_lon_lat 时，不应 raise，而应返回工具错误结构
3) 当模型未显式调用 get_device_location 但天气 location 为空，占位符检测应触发自动补定位
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class _FakeDeviceLocationResult:
    def __init__(self, *, ok: bool, result: Dict[str, Any] | None = None, error: str | None = None) -> None:
        self.ok = ok
        self.result = result
        self.error = error


class _FakeNetToolResult:
    def __init__(self, *, content_for_model: str, debug_payload: Dict[str, Any]) -> None:
        self.content_for_model = content_for_model
        self.debug_payload = debug_payload


@pytest.mark.anyio
async def test_tool_loop_uses_resolved_session_id_for_device_location_enabled(monkeypatch: Any) -> None:
    import socketio

    from backend_py.controllers.conversation_controller import ConversationController

    sio = socketio.AsyncServer(async_mode="asgi")
    controller = ConversationController(sio=sio)

    # 绑定 sid -> clientId
    sid = "SID_1"
    client_id = "web_test_1"
    controller.register_client(sid, client_id=client_id)

    # 只在 clientId session 上开启设备定位（模拟真实情况）
    client_session = controller.session_store.get_or_create(client_id)
    client_session.device_location_enabled = True
    client_session.device_location_set_at_ms = 1700000000100

    # sid session 仍是默认 False（如果 tool-loop 错用 sid，会读错）
    controller.session_store.get_or_create(sid).device_location_enabled = False

    captured: List[Dict[str, Any]] = []

    def _dump_debug_artifact(*, request_id: str, tag: str, payload: Dict[str, Any]) -> str:
        captured.append({"tag": tag, "payload": payload})
        return f"/tmp/{request_id}_{tag}.json"

    async def _fake_city_lookup(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {"code": "200", "location": [{"name": "test", "adm2": "x", "adm1": "y", "country": "z", "id": "1"}]}

    async def _fake_invoke_llm(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        # 结束 tool-loop（无下一轮 toolCalls）
        return {"text": "SAY: ok", "toolCalls": []}

    def _fake_get_current_location(*, timeout_sec: int = 12) -> _FakeDeviceLocationResult:
        return _FakeDeviceLocationResult(
            ok=True,
            result={
                "lon_lat": "113.99,22.67",
                "longitude": 113.99,
                "latitude": 22.67,
                "accuracy_m": 10,
                "timestamp_ms": 1700000000200,
                "address": {},
                "method": "test",
            },
        )

    monkeypatch.setattr(controller.network_tools_service, "dump_debug_artifact", _dump_debug_artifact)
    monkeypatch.setattr(controller.network_tools_service, "qweather_city_lookup", _fake_city_lookup)
    monkeypatch.setattr(controller.llm_service, "invoke_llm", _fake_invoke_llm)
    monkeypatch.setattr(controller.device_location_service, "get_current_location", _fake_get_current_location)

    llm_resp = {
        "text": "",
        "toolCalls": [
            {"id": "tc1", "type": "function", "function": {"name": "get_device_location", "arguments": "{}"}},
        ],
    }

    _ = await controller._maybe_run_network_tool_loop(
        sid=sid,
        request_id="req_test_1",
        user_text="今天天气怎么样",
        messages=[],
        llm_resp=llm_resp,
        tool_defs=[],
    )

    reqs = [x for x in captured if x["tag"] == "local_tool_request_get_device_location"]
    assert reqs, "应记录 local_tool_request_get_device_location"
    assert reqs[-1]["payload"].get("enabled") is True, "enabled 应从 clientId session 读取为 True"


@pytest.mark.anyio
async def test_weather_location_empty_triggers_auto_device_location(monkeypatch: Any) -> None:
    import socketio

    from backend_py.controllers.conversation_controller import ConversationController

    sio = socketio.AsyncServer(async_mode="asgi")
    controller = ConversationController(sio=sio)

    session = controller.session_store.get_or_create("web_test_2")
    session.device_location_enabled = True
    session.device_location_set_at_ms = 1700000000100

    captured: List[Dict[str, Any]] = []

    def _dump_debug_artifact(*, request_id: str, tag: str, payload: Dict[str, Any]) -> str:
        captured.append({"tag": tag, "payload": payload})
        return f"/tmp/{request_id}_{tag}.json"

    async def _fake_city_lookup(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {"code": "200", "location": [{"name": "test", "adm2": "x", "adm1": "y", "country": "z", "id": "1"}]}

    def _fake_get_current_location(*, timeout_sec: int = 12) -> _FakeDeviceLocationResult:
        return _FakeDeviceLocationResult(
            ok=True,
            result={
                "lon_lat": "113.99,22.67",
                "longitude": 113.99,
                "latitude": 22.67,
                "accuracy_m": 10,
                "timestamp_ms": 1700000000200,
                "address": {},
                "method": "test",
            },
        )

    async def _fake_execute(*, name: str, arguments: Dict[str, Any], request_id: str) -> _FakeNetToolResult:
        # 断言：location 应已被 override 成 device lon_lat
        if name in {"get_weather_now", "get_weather_12h"}:
            assert arguments.get("location") == "113.99,22.67"
        return _FakeNetToolResult(content_for_model='{"code":"200"}', debug_payload={"ok": True, "tool": name})

    async def _fake_invoke_llm(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {"text": "SAY: ok", "toolCalls": []}

    monkeypatch.setattr(controller.network_tools_service, "dump_debug_artifact", _dump_debug_artifact)
    monkeypatch.setattr(controller.network_tools_service, "qweather_city_lookup", _fake_city_lookup)
    monkeypatch.setattr(controller.network_tools_service, "execute", _fake_execute)
    monkeypatch.setattr(controller.device_location_service, "get_current_location", _fake_get_current_location)
    monkeypatch.setattr(controller.llm_service, "invoke_llm", _fake_invoke_llm)

    llm_resp = {
        "text": "",
        "toolCalls": [
            {"id": "w1", "type": "function", "function": {"name": "get_weather_now", "arguments": '{"location": ""}'}},
            {"id": "w2", "type": "function", "function": {"name": "get_weather_12h", "arguments": '{"location": ""}'}},
        ],
    }

    _ = await controller._maybe_run_network_tool_loop(
        sid="web_test_2",
        request_id="req_test_2",
        user_text="今天天气怎么样",
        messages=[],
        llm_resp=llm_resp,
        tool_defs=[],
    )

    reqs = [x for x in captured if x["tag"] == "local_tool_request_get_device_location"]
    assert reqs, "当天气 location 为空时，应自动补齐一次设备定位"


@pytest.mark.anyio
async def test_weather_location_empty_without_device_location_does_not_raise(monkeypatch: Any) -> None:
    """当 device_lon_lat 为空时，不应 raise 未捕获异常（应返回工具失败结构）。"""
    import socketio

    from backend_py.controllers.conversation_controller import ConversationController

    sio = socketio.AsyncServer(async_mode="asgi")
    controller = ConversationController(sio=sio)

    # 未开启设备定位
    session = controller.session_store.get_or_create("web_test_3")
    session.device_location_enabled = False
    session.device_location_set_at_ms = 1700000000100

    async def _fake_invoke_llm(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {"text": "SAY: ok", "toolCalls": []}

    async def _fake_execute(*, name: str, arguments: Dict[str, Any], request_id: str) -> _FakeNetToolResult:
        return _FakeNetToolResult(content_for_model='{"code":"401"}', debug_payload={"ok": False, "tool": name})

    monkeypatch.setattr(controller.llm_service, "invoke_llm", _fake_invoke_llm)
    monkeypatch.setattr(controller.network_tools_service, "execute", _fake_execute)
    monkeypatch.setattr(controller.network_tools_service, "dump_debug_artifact", lambda **kwargs: "/tmp/x.json")

    llm_resp = {
        "text": "",
        "toolCalls": [
            {"id": "w1", "type": "function", "function": {"name": "get_weather_now", "arguments": '{"location": ""}'}},
        ],
    }

    # 关键断言：不会抛异常
    _ = await controller._maybe_run_network_tool_loop(
        sid="web_test_3",
        request_id="req_test_3",
        user_text="今天天气怎么样",
        messages=[],
        llm_resp=llm_resp,
        tool_defs=[],
    )

