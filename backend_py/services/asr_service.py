from __future__ import annotations

import base64
import logging
from typing import Any, Dict

import httpx

from backend_py.config import settings


logger = logging.getLogger("backend_py.asr")


class ASRService:
    """语音转文字（STT）服务。

    当前实现：使用 DashScope OpenAI 兼容模式调用 `qwen3-omni-flash-2025-12-01`，通过
    `messages[].content` 的多模态输入（`type=input_audio`）实现转写。

    设计目标：
    - 维持现有 `transcribe(audio: bytes) -> str` 的调用方式
    - 不落盘音频文件
    - 日志输出 request_id（优先取响应头 `X-DashScope-Request-Id`）用于排障

    限制：
    - 单次音频大小限制：10MB（与旧实现一致）
    """

    _BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    _MODEL = "qwen3-omni-flash-2025-12-01"

    _SYSTEM_PROMPT = (
        "你是一个语音转写引擎。"
        "你必须将输入音频中的语音内容准确转写为文本。"
        "只输出转写结果，不要添加解释、不要补全不存在的内容。"
    )

    def __init__(self) -> None:
        self.api_key = settings.dashscope_api_key

    @staticmethod
    def _guess_mime(audio: bytes) -> str:
        if audio.startswith(b"RIFF") and b"WAVE" in audio[8:16]:
            return "audio/wav"
        if audio.startswith(b"ID3") or audio[:2] == b"\xff\xfb":
            return "audio/mpeg"
        if audio.startswith(b"\x1aE\xdf\xa3"):
            # EBML header (WebM)
            return "audio/webm"
        return "application/octet-stream"

    @staticmethod
    def _mime_to_audio_format(mime: str) -> str:
        """将 MIME 映射为兼容模式 `input_audio.format`。

        兼容模式要求提供 format（例如 wav/mp3/webm）。
        """

        m = str(mime or "").lower().strip()
        if m == "audio/wav":
            return "wav"
        if m == "audio/mpeg":
            return "mp3"
        if m == "audio/webm":
            return "webm"
        # 兜底：多数录音是 wav
        return "wav"

    @staticmethod
    def _extract_text(resp_json: Dict[str, Any]) -> str:
        """从 OpenAI 兼容模式响应中提取文本。"""

        choices = resp_json.get("choices") or []
        if not isinstance(choices, list) or not choices:
            return ""

        choice0 = choices[0] if isinstance(choices[0], dict) else {}
        msg = choice0.get("message") if isinstance(choice0, dict) else None
        msg = msg if isinstance(msg, dict) else {}

        content = msg.get("content")
        if isinstance(content, str):
            return content.strip()

        # 兼容 content 为数组（多模态）时的文本片段
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                # 兼容模式的文本片段通常是 {type: 'text', text: '...'}
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts).strip()

        return ""

    @staticmethod
    def _normalize_transcript(text: str) -> str:
        t = (text or "").strip()
        if not t:
            return ""

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

        # 去掉首尾引号
        if (t.startswith("'") and t.endswith("'")) or (t.startswith('"') and t.endswith('"')):
            t = t[1:-1].strip()

        return t

    def _build_payload(self, audio: bytes, *, language: str) -> tuple[Dict[str, Any], str, str]:
        """构造 STT 请求 payload。

        单独拆出来的原因：
        - 便于写离线自检脚本验证请求结构
        - 保持 `transcribe()` 主流程更清晰
        """

        mime = self._guess_mime(audio)
        audio_format = self._mime_to_audio_format(mime)

        audio_b64 = base64.b64encode(audio).decode("ascii")
        audio_data_url = f"data:{mime};base64,{audio_b64}"

        prompt = "请将音频内容转写为中文文本，只输出转写结果，不要添加解释。"
        if language and str(language).lower().startswith("en"):
            prompt = "Please transcribe the audio and output only the transcript."

        payload: Dict[str, Any] = {
            "model": self._MODEL,
            "messages": [
                {"role": "system", "content": self._SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": audio_data_url, "format": audio_format},
                        },
                        {"type": "text", "text": prompt},
                    ],
                },
            ],
            # 不开启 stream：STT 需要一次性拿到最终转写文本即可
            "stream": False,
        }

        return (payload, mime, audio_format)

    async def transcribe(self, audio: bytes, *, language: str = "zh-CN") -> str:
        if not self.api_key:
            raise RuntimeError("千问API密钥未配置，请设置DASHSCOPE_API_KEY环境变量")

        if not audio:
            return ""

        if len(audio) > 10 * 1024 * 1024:
            raise ValueError("音频文件过大（>10MB），请缩短录音时长")

        payload, mime, audio_format = self._build_payload(audio, language=language)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)

        async with httpx.AsyncClient(base_url=self._BASE_URL, timeout=timeout) as client:
            resp = await client.post("/chat/completions", headers=headers, json=payload)

            header_request_id = resp.headers.get("X-DashScope-Request-Id", "")

            try:
                data = resp.json()
            except Exception:
                data = {}

            # OpenAI 兼容响应通常有 `id`，但我们仍优先记录 DashScope 侧 request id
            response_id = data.get("id", "") if isinstance(data, dict) else ""

            if resp.status_code >= 400:
                error_msg = ""
                if isinstance(data, dict) and isinstance(data.get("error"), dict):
                    error_msg = str((data.get("error") or {}).get("message") or "")
                if not error_msg:
                    error_msg = (resp.text or "")[:200]

                logger.error(
                    "ASR请求失败 status=%d dashscopeRequestId=%s responseId=%s error=%s",
                    resp.status_code,
                    header_request_id,
                    response_id,
                    error_msg,
                )
                resp.raise_for_status()

        raw_text = self._extract_text(data if isinstance(data, dict) else {})
        text = self._normalize_transcript(raw_text)

        logger.info(
            "ASR完成 mime=%s format=%s bytes=%d text_len=%d dashscopeRequestId=%s responseId=%s",
            mime,
            audio_format,
            len(audio),
            len(text),
            header_request_id,
            response_id,
        )

        return text
