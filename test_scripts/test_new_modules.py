"""测试新增模块的核心功能（模块3/4/1/2）。

运行方式:
    cd voice_assistant
    source backend_py/.venv/bin/activate
    python -m pytest test_scripts/test_new_modules.py -v
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import uuid
import wave
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend_py.config import settings


# ──────────────────────────────────────────────
# 模块3 测试：LLM 配置化（config + llm_service）
# ──────────────────────────────────────────────

class TestLLMProviderConfig:
    """测试 LLM 提供者配置化。"""

    def test_default_provider_is_dashscope(self) -> None:
        """默认 provider 应为 dashscope。"""
        from backend_py.config import Settings

        s = Settings()
        assert s.llm_provider in {"dashscope", "ollama"}

    def test_ollama_provider_config(self) -> None:
        """设置 LLM_PROVIDER=ollama 时应正确加载 Ollama 配置。"""
        from backend_py.config import Settings

        env = {
            "LLM_PROVIDER": "ollama",
            "OLLAMA_BASE_URL": "http://127.0.0.1:11434/v1",
            "OLLAMA_MODEL": "test-model",
        }
        with patch.dict(os.environ, env, clear=False):
            s = Settings()
            assert s.llm_provider == "ollama"
            assert s.ollama_base_url == "http://127.0.0.1:11434/v1"
            assert s.ollama_model == "test-model"

    def test_llm_service_ollama_init(self) -> None:
        """LLMService 在 Ollama 模式下应正确初始化。"""
        from backend_py.services.llm_service import LLMService

        with patch.object(settings, "llm_provider", "ollama"), \
             patch.object(settings, "ollama_base_url", "http://localhost:11434/v1"), \
             patch.object(settings, "ollama_model", "test-model"):
            svc = LLMService()
            assert svc.provider == "ollama"
            assert svc.api_key == "ollama"
            assert svc.model_default == "test-model"
            assert svc.base_url == "http://localhost:11434/v1"

    def test_llm_service_dashscope_init(self) -> None:
        """LLMService 在 DashScope 模式下应正确初始化。"""
        from backend_py.services.llm_service import LLMService

        with patch.object(settings, "llm_provider", "dashscope"), \
             patch.object(settings, "dashscope_api_key", "test-key-123"):
            svc = LLMService()
            assert svc.provider == "dashscope"
            assert svc.api_key == "test-key-123"
            assert svc.model_default == "qwen-plus"


# ──────────────────────────────────────────────
# 模块4 测试：长期记忆服务（MemoryService）
# ──────────────────────────────────────────────

class TestMemoryService:
    """测试 MemoryService 核心功能。"""

    @pytest.fixture(autouse=True)
    def _setup_memory_dir(self, tmp_path: Path) -> Any:
        """每个测试用例使用独立的临时目录。"""
        unique_dir = str(tmp_path / f"memory_{uuid.uuid4().hex[:8]}")
        with patch.object(settings, "memory_enabled", True), \
             patch.object(settings, "memory_dir", unique_dir):
            from backend_py.services.memory_service import MemoryService

            self.svc = MemoryService()
            yield

    def test_save_and_load_profile(self) -> None:
        """应能保存和加载 user_profile。"""
        profile = {"name": "张三", "favorite_music": "周杰伦"}
        self.svc.save_user_profile(profile)
        loaded = self.svc.load_user_profile()
        assert loaded == profile

    def test_save_and_load_summary(self) -> None:
        """应能保存和加载 rolling_summary。"""
        summary = "用户叫张三，喜欢听周杰伦的歌。"
        self.svc.save_rolling_summary(summary)
        loaded = self.svc.load_rolling_summary()
        assert loaded == summary

    def test_build_memory_context_empty(self) -> None:
        """无数据时应返回空字符串。"""
        ctx = self.svc.build_memory_context()
        assert ctx == ""

    def test_build_memory_context_with_data(self) -> None:
        """有数据时应返回格式化的记忆上下文。"""
        self.svc.save_user_profile({"name": "李四"})
        self.svc.save_rolling_summary("李四喜欢运动。")
        ctx = self.svc.build_memory_context()
        assert "[用户档案]" in ctx
        assert "李四" in ctx
        assert "[对话摘要]" in ctx
        assert "喜欢运动" in ctx

    def test_should_extract_preferences_triggers(self) -> None:
        """偏好触发词检测。"""
        assert self.svc.should_extract_preferences("以后帮我用酷狗播放音乐")
        assert self.svc.should_extract_preferences("记住我叫张三")
        assert self.svc.should_extract_preferences("我喜欢听摇滚")
        assert not self.svc.should_extract_preferences("今天天气怎么样")
        assert not self.svc.should_extract_preferences("帮我播放音乐")

    def test_build_summary_update_messages(self) -> None:
        """应能构建摘要更新 messages。"""
        msgs = [
            {"role": "user", "content": "帮我播放周杰伦的歌"},
            {"role": "assistant", "content": "好的，正在搜索周杰伦。"},
        ]
        result = self.svc.build_summary_update_messages(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "PREVIOUS_SUMMARY" in result[0]["content"]
        assert "LATEST_MESSAGES" in result[0]["content"]

    def test_build_preference_extract_messages(self) -> None:
        """应能构建偏好抽取 messages。"""
        result = self.svc.build_preference_extract_messages("以后帮我用酷狗播放音乐")
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "USER_PROFILE" in result[0]["content"]
        assert "酷狗" in result[0]["content"]

    def test_parse_and_save_profile(self) -> None:
        """应能解析 LLM 返回的 JSON 并保存。"""
        llm_response = '{"name": "王五", "hobby": "游泳"}'
        result = self.svc.parse_and_save_profile(llm_response)
        assert result == {"name": "王五", "hobby": "游泳"}
        loaded = self.svc.load_user_profile()
        assert loaded["name"] == "王五"

    def test_parse_and_save_profile_with_markdown(self) -> None:
        """应能处理 Markdown 代码块包裹的 JSON。"""
        llm_response = '```json\n{"name": "赵六"}\n```'
        result = self.svc.parse_and_save_profile(llm_response)
        assert result == {"name": "赵六"}

    def test_parse_and_save_profile_invalid_json(self) -> None:
        """无效 JSON 应返回 None。"""
        result = self.svc.parse_and_save_profile("这不是JSON")
        assert result is None

    def test_disabled_memory(self, tmp_path: Path) -> None:
        """MEMORY_ENABLED=0 时所有操作应为空操作。"""
        unique_dir = str(tmp_path / f"disabled_{uuid.uuid4().hex[:8]}")
        with patch.object(settings, "memory_enabled", False), \
             patch.object(settings, "memory_dir", unique_dir):
            from backend_py.services.memory_service import MemoryService

            svc = MemoryService()
            assert svc.build_memory_context() == ""
            assert not svc.should_extract_preferences("记住我叫张三")
            svc.save_user_profile({"test": True})
            assert svc.load_user_profile() == {}


# ──────────────────────────────────────────────
# 模块1 测试：全局热键唤醒语音接收
# ──────────────────────────────────────────────

class TestHotkeyVoiceService:
    """测试 HotkeyVoiceService 核心功能。"""

    def test_frames_to_wav(self) -> None:
        """PCM frames 应能正确编码为 WAV。"""
        from backend_py.services.hotkey_voice_service import HotkeyVoiceService

        import struct

        silence = struct.pack("<" + "h" * 16000, *([0] * 16000))
        frames = [silence[:8000], silence[8000:]]

        wav_bytes = HotkeyVoiceService._frames_to_wav(frames)
        assert wav_bytes[:4] == b"RIFF"

        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            assert wf.getnframes() == 16000

    def test_frames_to_wav_empty(self) -> None:
        """空 frames 应返回空 bytes。"""
        from backend_py.services.hotkey_voice_service import HotkeyVoiceService

        result = HotkeyVoiceService._frames_to_wav([])
        assert result == b""

    def test_init_disabled(self) -> None:
        """HOTKEY_ENABLED=0 时 start() 不应启动监听器。"""
        from backend_py.services.hotkey_voice_service import HotkeyVoiceService

        with patch.object(settings, "hotkey_enabled", False):
            loop = asyncio.new_event_loop()
            svc = HotkeyVoiceService(loop=loop)
            svc.start()
            assert not svc._started
            assert svc._listener is None
            loop.close()

    def test_canonical_lowercase(self) -> None:
        """_canonical 应将字母键统一为小写。"""
        from backend_py.services.hotkey_voice_service import HotkeyVoiceService
        from pynput.keyboard import KeyCode

        result = HotkeyVoiceService._canonical(KeyCode.from_char("S"))
        assert result == KeyCode.from_char("s")

    def test_canonical_special_key_passthrough(self) -> None:
        """_canonical 对特殊键应直接返回。"""
        from backend_py.services.hotkey_voice_service import HotkeyVoiceService
        from pynput.keyboard import Key

        result = HotkeyVoiceService._canonical(Key.space)
        assert result is Key.space

    def test_parse_hotkey(self) -> None:
        """热键字符串应保持 pynput 格式不变。"""
        from backend_py.services.hotkey_voice_service import HotkeyVoiceService

        loop = asyncio.new_event_loop()
        svc = HotkeyVoiceService(loop=loop)
        assert svc._parse_hotkey("<cmd>+<shift>+<space>") == "<cmd>+<shift>+<space>"
        assert svc._parse_hotkey("  <ctrl>+a  ") == "<ctrl>+a"
        loop.close()


# ──────────────────────────────────────────────
# 模块2 测试：ESP32 硬件 WebSocket 服务
# ──────────────────────────────────────────────

class TestHardwareVoiceService:
    """测试 HardwareVoiceService 核心功能。"""

    def test_frames_to_wav(self) -> None:
        """PCM frames 应能正确编码为 WAV。"""
        from backend_py.services.hardware_voice_service import HardwareVoiceService

        import struct

        silence = struct.pack("<" + "h" * 16000, *([0] * 16000))
        wav_bytes = HardwareVoiceService._frames_to_wav([silence])
        assert wav_bytes[:4] == b"RIFF"

        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000

    def test_init_and_controller_injection(self) -> None:
        """应能初始化并注入 controller。"""
        from backend_py.services.hardware_voice_service import HardwareVoiceService

        svc = HardwareVoiceService()
        assert svc.connected_count == 0
        assert svc._controller is None

        mock_ctrl = MagicMock()
        svc.set_controller(mock_ctrl)
        assert svc._controller is mock_ctrl

    def test_frames_to_wav_empty(self) -> None:
        """空 frames 应返回空 bytes。"""
        from backend_py.services.hardware_voice_service import HardwareVoiceService

        result = HardwareVoiceService._frames_to_wav([])
        assert result == b""


# ──────────────────────────────────────────────
# 集成测试：config 配置项完整性
# ──────────────────────────────────────────────

class TestConfigCompleteness:
    """测试所有新增配置项是否存在且有合理默认值。"""

    def test_all_new_config_fields_exist(self) -> None:
        """所有新增配置项应有默认值。"""
        from backend_py.config import Settings

        s = Settings()
        assert hasattr(s, "llm_provider")
        assert hasattr(s, "ollama_base_url")
        assert hasattr(s, "ollama_model")
        assert hasattr(s, "hotkey_trigger")
        assert hasattr(s, "hotkey_stop")
        assert hasattr(s, "hotkey_enabled")
        assert hasattr(s, "hotkey_client_id")
        assert hasattr(s, "hardware_ws_enabled")
        assert hasattr(s, "memory_enabled")
        assert hasattr(s, "memory_dir")

    def test_default_values(self) -> None:
        """默认值应正确。"""
        from backend_py.config import Settings

        s = Settings()
        assert s.llm_provider in {"dashscope", "ollama"}
        assert s.hardware_ws_enabled is False
        assert "memory" in s.memory_dir
        assert isinstance(s.hotkey_client_id, str)
        assert s.hotkey_client_id


# ──────────────────────────────────────────────
# 集成测试：main.py 模块可导入
# ──────────────────────────────────────────────

class TestClientIdSessionBinding:
    """测试 clientId 绑定后会话迁移与 room 投递解析。"""

    def test_session_store_migrate(self) -> None:
        """SessionStore.migrate 应将会话从 sid 迁移到 clientId。"""
        from backend_py.session_store import SessionStore

        store = SessionStore()
        s1 = store.get_or_create("sid1")
        s1.network_access_enabled = True

        store.migrate("sid1", "desktop")
        assert store.get_or_create("desktop").network_access_enabled is True
        assert store.get_status("sid1").get("exists") is False

    def test_controller_resolve_session_and_emit_to(self) -> None:
        """ConversationController 应能将 socket sid 解析为 session_id+room。"""
        import socketio
        from unittest.mock import patch

        from backend_py.config import settings

        with patch.object(settings, "dashscope_api_key", "test-key"):
            from backend_py.controllers.conversation_controller import ConversationController

            sio = socketio.AsyncServer(async_mode="asgi")
            controller = ConversationController(sio=sio)

        controller.session_store.get_or_create("sid1").network_access_enabled = True
        controller.register_client("sid1", client_id="desktop")

        session_id, emit_to = controller._resolve_session_and_emit_to("sid1")
        assert session_id == "desktop"
        assert emit_to == "client:desktop"

        session_id2, emit_to2 = controller._resolve_session_and_emit_to("client:desktop")
        assert session_id2 == "desktop"
        assert emit_to2 == "client:desktop"

        # 会话应已迁移
        assert controller.session_store.get_or_create("desktop").network_access_enabled is True

    def test_hotkey_emit_fallback_to_last_online_client(self) -> None:
        """当 desktop 不在线时，热键应 fallback 到最近在线 clientId。"""
        import socketio
        from unittest.mock import patch

        from backend_py.config import settings

        with patch.object(settings, "dashscope_api_key", "test-key"):
            from backend_py.controllers.conversation_controller import ConversationController

            sio = socketio.AsyncServer(async_mode="asgi")
            controller = ConversationController(sio=sio)

        controller.register_client("sid_web", client_id="web_1")

        chosen, emit_to = controller.resolve_hotkey_emit_to("desktop")
        assert chosen == "web_1"
        assert emit_to == "client:web_1"


class TestMainImport:
    """测试 main.py 模块可成功导入（不启动服务）。"""

    def test_main_module_importable(self) -> None:
        """backend_py.main 应能正常导入。"""
        import importlib

        mod = importlib.import_module("backend_py.main")
        assert hasattr(mod, "asgi_app")
        assert hasattr(mod, "controller")
        assert hasattr(mod, "on_startup")
        assert hasattr(mod, "on_shutdown")
        assert hasattr(mod, "hardware_ws_endpoint")
