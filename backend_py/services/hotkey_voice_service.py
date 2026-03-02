"""全局热键唤醒语音接收服务。

功能：
- 监听全局热键（默认 Cmd+Shift+Space）切换录音状态（toggle 模式）
- 第一次按下开始录音，再次按下停止录音并处理音频
- 录音完成后将 WAV 音频注入 ConversationController.handle_voice_input 链路（绑定到指定 clientId）
- 通过 pynput 实现全局热键监听（无需浏览器窗口聚焦）
- 通过 sounddevice 采集麦克风音频

注意：
  pynput 1.8.x 的 GlobalHotKeys 在 macOS 上存在 bug：
  _darwin.py Listener._handle_message 的媒体键分支调用 on_press(key) 时
  缺少 injected 参数，导致 GlobalHotKeys._on_press(self, key, injected) 崩溃。
  因此本模块使用底层 Listener + HotKey 手动组合，回调签名用 *args 兼容。

  macOS 上 pynput 无法拦截按键事件传递给前台应用（需要 CGEventTap active tap），
  因此停止录音不再使用单独热键（如 Cmd+Shift+S 会与系统"另存为"冲突），
  而是采用 toggle 模式：同一个热键按第一次开始、按第二次停止。
"""
from __future__ import annotations

import asyncio
import io
import logging
import wave
from typing import TYPE_CHECKING, Optional

from backend_py.config import settings

if TYPE_CHECKING:
    from backend_py.controllers.conversation_controller import ConversationController

logger = logging.getLogger("backend_py.hotkey_voice")

# 录音参数
SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"


class HotkeyVoiceService:
    """全局热键唤醒语音接收（toggle 模式）。

    生命周期：
    - start()：注册全局热键监听器（后台线程）
    - stop()：注销监听器、释放资源
    - 按热键切换录音状态：未录音 → 开始录音；录音中 → 停止并处理音频
    """

    def __init__(self, *, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._controller: Optional[ConversationController] = None
        self._recording = False
        self._audio_frames: list[bytes] = []
        self._stream: Optional[object] = None
        self._listener: Optional[object] = None
        self._started = False

    def set_controller(self, controller: ConversationController) -> None:
        """延迟注入 controller（避免循环依赖）。"""
        self._controller = controller

    def start(self) -> None:
        """启动全局热键监听。"""
        if not settings.hotkey_enabled:
            logger.info("全局热键已禁用 (HOTKEY_ENABLED=0)")
            return

        if self._started:
            return

        try:
            from pynput.keyboard import HotKey, Listener
        except ImportError:
            logger.error("pynput 未安装，全局热键不可用。请执行: pip install pynput")
            return

        trigger_str = settings.hotkey_trigger.strip()

        # 构建 HotKey 对象（仅一个 toggle 热键）
        hotkeys: list = []
        try:
            if trigger_str:
                hotkeys.append(
                    HotKey(HotKey.parse(trigger_str), self._on_toggle)
                )
        except ValueError as e:
            logger.error("热键格式解析失败: %s", e)
            return

        if not hotkeys:
            logger.warning("未配置有效热键，跳过启动")
            return

        # 使用底层 Listener + HotKey.press/release 手动分发
        # 回调签名用 *args 兼容 pynput 1.8.x 漏传 injected 参数的 bug
        # 关键：必须使用 Listener.canonical() 将按键统一为规范形式，
        # 否则 Key.space（枚举）无法匹配 HotKey.parse 产出的 KeyCode(vk=49)。
        captured_self = self

        def on_press(key: object, *args: object) -> None:
            if captured_self._listener is None:
                return
            canonical_key = captured_self._listener.canonical(key)
            for hk in hotkeys:
                try:
                    hk.press(canonical_key)
                except Exception:
                    pass

        def on_release(key: object, *args: object) -> None:
            if captured_self._listener is None:
                return
            canonical_key = captured_self._listener.canonical(key)
            for hk in hotkeys:
                try:
                    hk.release(canonical_key)
                except Exception:
                    pass

        self._listener = Listener(on_press=on_press, on_release=on_release)
        self._listener.start()
        self._started = True
        logger.info(
            "全局热键已启动 (toggle 模式): hotkey=%s",
            trigger_str,
        )

    def stop(self) -> None:
        """停止全局热键监听并释放资源。"""
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

        self._stop_recording()
        self._started = False
        logger.info("全局热键已停止")

    @staticmethod
    def _canonical(key: object) -> object:
        """简易按键规范化（已弃用，保留供测试兼容）。

        注意：此方法无法正确处理 Key.space → KeyCode(vk=49) 的转换，
        实际热键匹配已改用 Listener.canonical() 实例方法。
        """
        try:
            from pynput.keyboard import KeyCode

            if isinstance(key, KeyCode) and key.char is not None:
                return KeyCode.from_char(key.char.lower())
        except Exception:
            pass
        return key

    @staticmethod
    def _parse_hotkey(hotkey_str: str) -> str:
        """将配置格式转换为 pynput 格式（保持原样）。"""
        return hotkey_str.strip()

    def _on_toggle(self) -> None:
        """Toggle 热键回调：切换录音状态。"""
        if self._recording:
            logger.info("热键触发：停止录音")
            self._stop_recording_and_process()
        else:
            logger.info("热键触发：开始录音")
            self._start_recording()

    def _start_recording(self) -> None:
        """开始麦克风录音。"""
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError:
            logger.error("sounddevice/numpy 未安装，录音不可用")
            return

        self._audio_frames = []
        self._recording = True

        def audio_callback(indata: np.ndarray, frames: int, time_info: object, status: object) -> None:
            if status:
                logger.warning("录音状态: %s", status)
            if self._recording:
                self._audio_frames.append(indata.copy().tobytes())

        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                callback=audio_callback,
                blocksize=1024,
            )
            self._stream.start()
            logger.info("麦克风录音已开始 (rate=%d, channels=%d)", SAMPLE_RATE, CHANNELS)
        except Exception as e:
            logger.error("打开麦克风失败: %s", e)
            self._recording = False

    def _stop_recording(self) -> None:
        """停止录音流（不触发处理）。"""
        self._recording = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _stop_recording_and_process(self) -> None:
        """停止录音并将音频发送到主链路处理。"""
        self._recording = False

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        if not self._audio_frames:
            logger.warning("录音为空，跳过处理")
            return

        wav_bytes = self._frames_to_wav(self._audio_frames)
        self._audio_frames = []

        if not wav_bytes:
            logger.warning("WAV 编码失败，跳过处理")
            return

        logger.info("录音完成，WAV 大小: %d bytes", len(wav_bytes))

        # 在事件循环中异步处理音频
        asyncio.run_coroutine_threadsafe(
            self._process_audio(wav_bytes),
            self._loop,
        )

    async def _process_audio(self, wav_bytes: bytes) -> None:
        """将录音注入 ConversationController.handle_voice_input 链路。

        热键录音不会创建独立 session，而是绑定到指定 clientId（默认 desktop），
        与 Web 端共享同一套配置/权限/音色，并将 TTS 音频推送给该客户端播放。
        """
        if self._controller is None:
            logger.error("controller 未注入，无法处理音频")
            return

        preferred_client_id = getattr(settings, "hotkey_client_id", "desktop") or "desktop"
        chosen_client_id, emit_to = self._controller.resolve_hotkey_emit_to(preferred_client_id)

        try:
            await self._controller.handle_voice_input(
                sid=emit_to,
                audio_data=wav_bytes,
                language="zh-CN",
                request_id=None,
            )
        except Exception as e:
            logger.error("热键录音处理失败: %s", e)

    @staticmethod
    def _frames_to_wav(frames: list[bytes]) -> bytes:
        """将 PCM frames 编码为 WAV 格式。"""
        pcm_data = b"".join(frames)
        if not pcm_data:
            return b""

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # int16 = 2 bytes
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm_data)

        return buf.getvalue()
