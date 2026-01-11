from __future__ import annotations

import base64
import logging
from typing import Any, Dict, Optional

import httpx

from backend_py.config import settings


logger = logging.getLogger("backend_py.asr")


class ASRService:
    """Speech-to-text service using Qwen Audio (DashScope native protocol).

    It sends audio as Data URL base64 to DashScope multimodal-generation endpoint.

    Limits (per official docs):
    - audio duration: up to 30s (model may only process the first 30s)
    - audio size: up to 10MB
    """

    def __init__(self) -> None:
        self.api_key = settings.dashscope_api_key
        self.endpoint = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        self.model = "qwen-audio-turbo-latest"

    @staticmethod
    def _guess_mime(audio: bytes) -> str:
        if audio.startswith(b"RIFF") and b"WAVE" in audio[8:16]:
            return "audio/wav"
        if audio.startswith(b"ID3") or audio[:2] == b"\xff\xfb":
            return "audio/mpeg"
        if audio.startswith(b"\x1aE\xdf\xa3"):
            # EBML header
            return "audio/webm"
        return "application/octet-stream"

    @staticmethod
    def _extract_text(resp_json: Dict[str, Any]) -> str:
        choices = ((resp_json.get("output") or {}).get("choices") or [])
        if not choices:
            return ""

        msg = (choices[0].get("message") or {})
        content = msg.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts).strip()

        if isinstance(content, str):
            return content.strip()

        return ""

    @staticmethod
    def _normalize_transcript(text: str) -> str:
        t = (text or "").strip()
        if not t:
            return ""

        # Some models respond like: 这段音频说的是:'xxx'
        prefixes = [
            "这段音频说的是:",
            "这段音频说的是：",
            "音频内容是:",
            "音频内容是：",
            "转写结果:",
            "转写结果：",
            "转录结果:",
            "转录结果：",
        ]
        for p in prefixes:
            if t.startswith(p):
                t = t[len(p) :].strip()
                break

        # Strip surrounding quotes
        if (t.startswith("'") and t.endswith("'")) or (t.startswith('"') and t.endswith('"')):
            t = t[1:-1].strip()

        return t

    async def transcribe(self, audio: bytes, *, language: str = "zh-CN") -> str:
        if not self.api_key:
            raise RuntimeError("千问API密钥未配置，请设置DASHSCOPE_API_KEY环境变量")

        if not audio:
            return ""

        if len(audio) > 10 * 1024 * 1024:
            raise ValueError("音频文件过大（>10MB），请缩短录音时长")

        mime = self._guess_mime(audio)
        audio_b64 = base64.b64encode(audio).decode("ascii")
        audio_data_url = f"data:{mime};base64,{audio_b64}"

        prompt = "请将音频内容转写为中文文本，只输出转写结果，不要添加解释。"
        if language and language.lower().startswith("en"):
            prompt = "Please transcribe the audio and output only the transcript."

        payload: Dict[str, Any] = {
            "model": self.model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"audio": audio_data_url},
                            {"text": prompt},
                        ],
                    }
                ]
            },
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(self.endpoint, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        raw_text = self._extract_text(data)
        text = self._normalize_transcript(raw_text)

        logger.info(
            "ASR完成 mime=%s bytes=%d text_len=%d request_id=%s",
            mime,
            len(audio),
            len(text),
            data.get("request_id"),
        )

        return text
