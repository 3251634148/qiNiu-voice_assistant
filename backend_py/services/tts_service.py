from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from backend_py.config import settings
from backend_py.services.qwen_tts_ws import QwenTTSWebSocket


logger = logging.getLogger("backend_py.tts")


class TTSService:
    def __init__(self) -> None:
        self.api_key = settings.dashscope_api_key
        if not self.api_key:
            logger.warning("未设置DASHSCOPE_API_KEY环境变量，TTS功能将不可用")
        self.qwen = QwenTTSWebSocket(self.api_key) if self.api_key else None

    @staticmethod
    def map_voice_from_gender(gender: str) -> str:
        return {"female": "zhizhe_emo", "male": "zhishuo_emo"}.get(gender, "zhizhe_emo")

    def get_available_voices(self) -> list[dict[str, Any]]:
        return [
            {"id": "female", "name": "女声", "provider": "qwen"},
            {"id": "male", "name": "男声", "provider": "qwen"},
        ]

    async def text_to_speech(
        self,
        text: str,
        voice_settings: Dict[str, Any],
        *,
        on_audio_chunk: Optional[Callable[[bytes], Awaitable[None]]] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> bytes:
        if not self.qwen:
            return b""

        cancel = cancel_event or asyncio.Event()
        settings_obj = {
            "voice": voice_settings.get("voice")
            or self.map_voice_from_gender(voice_settings.get("gender") or "female"),
            "rate": voice_settings.get("rate") or 1.0,
            "pitch": voice_settings.get("pitch") or 1.0,
            "model": voice_settings.get("model") or None,
        }

        try:
            if on_audio_chunk:
                result = await self.qwen.stream_synthesize(text, settings_obj, on_audio_chunk, cancel_event=cancel)
                return result.full_audio
            return await self.qwen.synthesize(text, settings_obj, cancel_event=cancel)
        except Exception as e:
            logger.exception("千问TTS失败: %s", e)
            return b""
