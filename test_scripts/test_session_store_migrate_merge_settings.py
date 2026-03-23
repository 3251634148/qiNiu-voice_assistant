"""回归测试：修复 sid->clientId 迁移时会话态丢失（根因）。

背景（用户现象）：
- 前端在连接后会同步“设备定位/联网/TTS”等开关到后端 session（通常落在 socket sid 上）
- 随后再通过 `register-client` 把 sid 绑定到稳定 clientId，并触发 SessionStore.migrate(sid->clientId)
- 若 clientId 的 session 已存在，旧实现会直接丢弃 sid session，导致刚同步的开关/音色丢失

本文件的用例用于稳定复现并证明该根因，同时验证修复后的合并迁移行为。

运行方式：
    cd voice_assistant
    source backend_py/.venv/bin/activate
    python -m pytest test_scripts/test_session_store_migrate_merge_settings.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class TestSessionStoreMigrateMerge:
    def test_migrate_merges_settings_when_target_exists(self) -> None:
        """当 to_id 已存在时，migrate 应合并关键会话态而非丢弃 from_id。"""
        from backend_py.session_store import SessionStore

        store = SessionStore()

        # 目标会话已存在（模拟：某次重连/多连接/历史残留，使 clientId 会话早已存在）
        dst = store.get_or_create("desktop")
        dst.network_access_enabled = False
        dst.device_location_enabled = False
        dst.tts_settings = {"voice": "Cherry"}  # 模拟已有音色

        # 源会话承载了“刚同步的开关”（模拟：update-device-location / update-network-settings 先到）
        src = store.get_or_create("sid_new")
        src.network_access_enabled = True
        src.network_access_set_at_ms = 1700000000100
        src.device_location_enabled = True
        src.device_location_set_at_ms = 1700000000200
        src.device_location = {"lonLat": "120.12,30.28", "tsMs": 1700000000000}
        src.tts_settings = {"voice": "Ethan", "rate": 1.2}

        store.migrate("sid_new", "desktop")

        # 关键断言：开关应被合并到 desktop 会话中
        merged = store.get_or_create("desktop")
        assert merged.network_access_enabled is True
        assert merged.device_location_enabled is True

        # device_location：dst 原本没有 -> 应补齐
        assert isinstance(merged.device_location, dict)
        assert merged.device_location.get("lonLat") == "120.12,30.28"

        # tts_settings：dst 已有 voice 不应被 src 覆盖，但缺失字段应补齐
        assert merged.tts_settings.get("voice") == "Cherry"
        assert merged.tts_settings.get("rate") == 1.2

        # sid_new 会话应被删除
        assert store.get_status("sid_new").get("exists") is False

    def test_controller_flow_update_then_register_does_not_lose_device_location_enabled(self) -> None:
        """模拟真实链路：update_device_location(落到sid) -> register_client(migrate) 不应丢失开关。"""
        import socketio
        from unittest.mock import patch

        from backend_py.config import settings

        with patch.object(settings, "dashscope_api_key", "test-key"):
            from backend_py.controllers.conversation_controller import ConversationController

            sio = socketio.AsyncServer(async_mode="asgi")
            controller = ConversationController(sio=sio)

        # 先创建一个已存在的 clientId 会话（模拟：历史遗留/重连导致 desktop session 已存在）
        controller.session_store.get_or_create("desktop").device_location_enabled = False

        # sid_new 收到前端同步开关（发生在 register-client 之前）
        r = controller.update_device_location("sid_new", {"deviceLocationEnabled": True})
        assert r.get("success") is True

        # 绑定到 desktop，触发 migrate(sid_new -> desktop)
        controller.register_client("sid_new", client_id="desktop")

        merged = controller.session_store.get_or_create("desktop")
        assert merged.device_location_enabled is True


@pytest.mark.parametrize(
    "dst_enabled,dst_ts,src_enabled,src_ts,expected",
    [
        # src 无显式设置（ts=0）不应覆盖 dst
        (True, 1700000000000, False, 0, True),
        (False, 1700000000000, True, 0, False),
        # src 显式设置更新更晚（ts 更大）应覆盖 dst（支持开启与关闭）
        (False, 1700000000000, True, 1700000000100, True),
        (True, 1700000000000, False, 1700000000100, False),
        # src 显式设置更早不应覆盖 dst
        (True, 1700000000100, False, 1700000000000, True),
        (False, 1700000000100, True, 1700000000000, False),
    ],
)
def test_merge_bool_uses_last_set_ts(
    dst_enabled: bool,
    dst_ts: int,
    src_enabled: bool,
    src_ts: int,
    expected: bool,
) -> None:
    """布尔开关合并策略：以“最后一次显式设置时间戳”为准（避免时序竞争导致开关丢失）。"""
    from backend_py.session_store import SessionStore

    store = SessionStore()
    dst = store.get_or_create("desktop")
    src = store.get_or_create("sid_new")

    dst.device_location_enabled = dst_enabled
    dst.device_location_set_at_ms = dst_ts
    src.device_location_enabled = src_enabled
    src.device_location_set_at_ms = src_ts

    store.migrate("sid_new", "desktop")
    assert store.get_or_create("desktop").device_location_enabled is expected

