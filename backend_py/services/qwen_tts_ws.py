from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

import websockets

from backend_py.utils.wav import create_wav_header


logger = logging.getLogger("backend_py.qwen_tts")


@dataclass
class StreamResult:
    full_audio: bytes
    success: bool
    task_id: Optional[str] = None


class QwenTTSWebSocket:
    """DashScope TTS WebSocket client.

    Matches Node implementation in backend/services/qwenTTSWebSocket.js.
    """

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.ws_url = "wss://dashscope.aliyuncs.com/api-ws/v1/inference/"

        self.sample_rate = 16000
        self.bits_per_sample = 16
        self.channels = 1

    async def stream_synthesize(
        self,
        text: str,
        settings: Dict[str, Any],
        on_audio_chunk: Callable[[bytes], Awaitable[None]],
        *,
        cancel_event: asyncio.Event,
    ) -> StreamResult:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-DashScope-WorkMode": "async",
        }

        task_id = f"task_{int(time.time() * 1000)}_{random.randint(100000, 999999)}"
        request = {
            "header": {
                "action": "run-task",
                "task_id": task_id,
                "streaming": "out",
            },
            "payload": {
                "model": settings.get("model") or "sambert-zhichu-v1",
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "input": {"text": text},
                "parameters": {
                    "text_type": "PlainText",
                    "format": "wav",
                    "sample_rate": self.sample_rate,
                    "volume": 50,
                    "rate": float(max(0.5, min(2.0, settings.get("rate", 1.0)))),
                    "pitch": float(max(0.5, min(2.0, settings.get("pitch", 1.0)))),
                    "word_timestamp_enabled": False,
                    "phoneme_timestamp_enabled": False,
                    "voice": settings.get("voice") or "zhizhe_emo",
                },
            },
        }

        full_pcm_parts: list[bytes] = []
        pending_pcm_parts: list[bytes] = []
        pending_size = 0
        is_first = True

        connect_kwargs: Dict[str, Any] = {
            "ping_interval": None,
        }

        # websockets>=12 使用 additional_headers；旧版本使用 extra_headers
        connect_sig = inspect.signature(websockets.connect)
        if "additional_headers" in connect_sig.parameters:
            connect_kwargs["additional_headers"] = headers
        else:
            connect_kwargs["extra_headers"] = headers

        async with websockets.connect(self.ws_url, **connect_kwargs) as ws:
            await ws.send(json.dumps(request, ensure_ascii=False))

            while True:
                if cancel_event.is_set():
                    logger.info("TTS canceled; stop reading ws")
                    break

                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                except asyncio.TimeoutError:
                    logger.warning("TTS ws recv timeout")
                    break

                if isinstance(msg, str):
                    try:
                        obj = json.loads(msg)
                    except Exception:
                        continue

                    header = obj.get("header") or {}
                    event = header.get("event")
                    if event == "task-started":
                        logger.info("TTS task started %s", header.get("task_id"))
                    elif event == "task-finished":
                        logger.info("TTS task finished")
                        break
                    elif event == "task-failed":
                        logger.error("TTS task failed %s", obj)
                        break
                    continue

                if isinstance(msg, (bytes, bytearray)):
                    chunk = bytes(msg)
                    if not chunk:
                        continue

                    full_pcm_parts.append(chunk)
                    pending_pcm_parts.append(chunk)
                    pending_size += len(chunk)

                    first_threshold = 4000
                    next_threshold = 12000
                    threshold = first_threshold if is_first else next_threshold

                    if pending_size >= threshold:
                        pcm = b"".join(pending_pcm_parts)
                        wav = create_wav_header(len(pcm), sample_rate=self.sample_rate) + pcm
                        await on_audio_chunk(wav)
                        is_first = False
                        pending_pcm_parts = []
                        pending_size = 0

            # flush remaining
            if not cancel_event.is_set() and pending_pcm_parts:
                pcm = b"".join(pending_pcm_parts)
                wav = create_wav_header(len(pcm), sample_rate=self.sample_rate) + pcm
                await on_audio_chunk(wav)

        full_pcm = b"".join(full_pcm_parts)
        full_wav = create_wav_header(len(full_pcm), sample_rate=self.sample_rate) + full_pcm if full_pcm else b""

        return StreamResult(full_audio=full_wav, success=bool(full_pcm), task_id=task_id)

    async def synthesize(self, text: str, settings: Dict[str, Any], *, cancel_event: asyncio.Event) -> bytes:
        chunks: list[bytes] = []

        async def _collect(chunk: bytes) -> None:
            chunks.append(chunk)

        result = await self.stream_synthesize(text, settings, _collect, cancel_event=cancel_event)
        return result.full_audio
