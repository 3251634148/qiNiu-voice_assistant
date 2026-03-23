"""ESP32 硬件语音模块 WebSocket 对接服务。

通信协议：
- 连接端点：ws://<host>:<port>/ws/hardware
- ESP32 → 服务端：
  - 二进制帧：PCM 音频数据（16kHz, mono, int16）
  - JSON 文本帧：{"type": "start_record"} / {"type": "stop_record"} / {"type": "ping"}
- 服务端 → ESP32：
  - 二进制帧：TTS 音频数据（WAV 或 PCM）
  - JSON 文本帧：{"type": "text", "content": "..."} / {"type": "pong"} / {"type": "status", ...}

ESP32 硬件参考：
- 麦克风：INMP441（I2S 输入，16kHz 采样）
- 功放：MAX98357A（I2S 输出）
- 主控：ESP32-S3-N16R8
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import time
import wave
from typing import TYPE_CHECKING, Any, Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect

from backend_py.config import settings

if TYPE_CHECKING:
    from backend_py.controllers.conversation_controller import ConversationController

logger = logging.getLogger("backend_py.hardware_voice")

# 音频参数（与 ESP32 INMP441 对齐）
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # int16


class HardwareVoiceService:
    """管理 ESP32 硬件模块的 WebSocket 连接和双向音频传输。"""

    def __init__(self) -> None:
        self._controller: Optional[ConversationController] = None
        self._active_connections: Dict[str, WebSocket] = {}

    def set_controller(self, controller: ConversationController) -> None:
        """延迟注入 controller（避免循环依赖）。"""
        self._controller = controller

    @property
    def connected_count(self) -> int:
        """当前连接的硬件设备数。"""
        return len(self._active_connections)

    async def handle_websocket(self, websocket: WebSocket) -> None:
        """处理单个 ESP32 WebSocket 连接的完整生命周期。"""
        await websocket.accept()
        conn_id = f"hw_{id(websocket)}_{int(time.time() * 1000)}"
        self._active_connections[conn_id] = websocket
        logger.info("硬件设备已连接: %s (当前 %d 台)", conn_id, self.connected_count)

        audio_buffer: list[bytes] = []
        is_recording = False

        try:
            while True:
                message = await websocket.receive()

                if "bytes" in message and message["bytes"]:
                    # 二进制帧：PCM 音频数据
                    if is_recording:
                        audio_buffer.append(message["bytes"])
                    continue

                if "text" in message and message["text"]:
                    # JSON 文本帧
                    try:
                        data = json.loads(message["text"])
                    except json.JSONDecodeError:
                        logger.warning("无效 JSON: %s", message["text"][:200])
                        continue

                    msg_type = str(data.get("type") or "").strip()

                    if msg_type == "start_record":
                        is_recording = True
                        audio_buffer = []
                        logger.info("硬件设备开始录音: %s", conn_id)
                        await self._send_json(websocket, {"type": "status", "recording": True})

                    elif msg_type == "stop_record":
                        is_recording = False
                        logger.info(
                            "硬件设备停止录音: %s (收到 %d 帧)",
                            conn_id,
                            len(audio_buffer),
                        )
                        await self._send_json(websocket, {"type": "status", "recording": False})

                        if audio_buffer:
                            wav_bytes = self._frames_to_wav(audio_buffer)
                            audio_buffer = []
                            if wav_bytes:
                                await self._process_audio(conn_id, websocket, wav_bytes)

                    elif msg_type == "ping":
                        await self._send_json(websocket, {"type": "pong", "ts": time.time()})

                    elif msg_type == "text_command":
                        # ESP32 也可以直接发送文本命令（如离线 ASR 后的文本）
                        text = str(data.get("text") or "").strip()
                        if text and self._controller is not None:
                            await self._process_text(conn_id, websocket, text)

        except WebSocketDisconnect:
            logger.info("硬件设备断开连接: %s", conn_id)
        except Exception as e:
            logger.error("硬件 WebSocket 异常: %s - %s", conn_id, e)
        finally:
            self._active_connections.pop(conn_id, None)
            logger.info("硬件设备清理完成: %s (剩余 %d 台)", conn_id, self.connected_count)

    async def send_tts_audio(self, conn_id: str, audio_data: bytes) -> None:
        """向指定硬件设备发送 TTS 音频数据。"""
        ws = self._active_connections.get(conn_id)
        if ws is None:
            logger.warning("设备 %s 不在线，无法发送音频", conn_id)
            return
        try:
            await ws.send_bytes(audio_data)
        except Exception as e:
            logger.warning("发送音频到 %s 失败: %s", conn_id, e)

    async def broadcast_tts_audio(self, audio_data: bytes) -> None:
        """向所有连接的硬件设备广播 TTS 音频。"""
        for conn_id in list(self._active_connections.keys()):
            await self.send_tts_audio(conn_id, audio_data)

    async def _process_audio(self, conn_id: str, websocket: WebSocket, wav_bytes: bytes) -> None:
        """将硬件录音注入 ConversationController.handle_voice_input 链路。"""
        if self._controller is None:
            logger.error("controller 未注入，无法处理硬件音频")
            await self._send_json(websocket, {
                "type": "error",
                "message": "服务未就绪",
            })
            return

        sid = f"__hardware_{conn_id}__"
        try:
            # 通知设备正在处理
            await self._send_json(websocket, {"type": "status", "processing": True})

            await self._controller.handle_voice_input(
                sid=sid,
                audio_data=wav_bytes,
                language="zh-CN",
                request_id=None,
            )

            await self._send_json(websocket, {"type": "status", "processing": False})
        except Exception as e:
            logger.error("硬件音频处理失败: %s - %s", conn_id, e)
            await self._send_json(websocket, {
                "type": "error",
                "message": f"处理失败: {e}",
            })

    async def _process_text(self, conn_id: str, websocket: WebSocket, text: str) -> None:
        """处理硬件设备发送的文本命令。"""
        if self._controller is None:
            logger.error("controller 未注入，无法处理硬件文本命令")
            return

        sid = f"__hardware_{conn_id}__"
        try:
            await self._send_json(websocket, {"type": "status", "processing": True})
            await self._controller.handle_text_command(
                sid=sid,
                text=text,
                request_id=None,
            )
            await self._send_json(websocket, {"type": "status", "processing": False})
        except Exception as e:
            logger.error("硬件文本命令处理失败: %s - %s", conn_id, e)

    @staticmethod
    async def _send_json(websocket: WebSocket, data: Dict[str, Any]) -> None:
        """发送 JSON 文本帧到 WebSocket。"""
        try:
            await websocket.send_text(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass

    @staticmethod
    def _frames_to_wav(frames: list[bytes]) -> bytes:
        """将 PCM frames 编码为 WAV 格式。"""
        pcm_data = b"".join(frames)
        if not pcm_data:
            return b""

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm_data)

        return buf.getvalue()
