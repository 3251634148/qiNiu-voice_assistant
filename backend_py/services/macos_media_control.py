from __future__ import annotations

import asyncio
import subprocess
from typing import Any, Dict, Optional


class MacOSMediaControl:
    """System media fallback control on macOS.

    Goals:
    - Provide a "good enough" fallback using media keys / system volume.
    - Prefer system-wide controls; for "now playing" we do best-effort per-player.

    Notes:
    - Media key injection uses `NSEventTypeSystemDefined` events (requires Accessibility permission).
    - "当前曲目信息" is best-effort and depends on the active player integration.
    """

    KEY_MAP = {
        "play_pause": 16,  # NX_KEYTYPE_PLAY
        "next": 17,  # NX_KEYTYPE_NEXT
        "previous": 18,  # NX_KEYTYPE_PREVIOUS
        "mute": 7,  # NX_KEYTYPE_MUTE
        "volume_down": 1,  # NX_KEYTYPE_SOUND_DOWN
        "volume_up": 0,  # NX_KEYTYPE_SOUND_UP
    }

    def __init__(self) -> None:
        self._ensure_darwin()

    @staticmethod
    def _ensure_darwin() -> None:
        import platform

        if platform.system().lower() != "darwin":
            raise RuntimeError("MacOSMediaControl 仅支持 macOS")

    @staticmethod
    def _osascript(script: str) -> str:
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "osascript 执行失败")
        return proc.stdout.strip()

    @staticmethod
    def _post_media_key_sync(key_code: int) -> None:
        try:
            from AppKit import NSEvent, NSSystemDefined
            from Quartz import CGEventPost, CGPoint, kCGHIDEventTap
        except Exception as e:
            raise RuntimeError(
                "媒体键依赖未安装或不可用：请安装 PyObjC（Cocoa/Quartz）。\n"
                f"原始错误：{e}"
            )

        def _event(down: bool) -> Any:
            # Based on common recipes for media key events.
            flags = 0xA00 if down else 0xB00
            data1 = (int(key_code) << 16) | ((0xA if down else 0xB) << 8)
            return NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                NSSystemDefined,
                CGPoint(0, 0),
                flags,
                0,
                0,
                None,
                8,
                data1,
                -1,
            )

        for down in [True, False]:
            ev = _event(down)
            cg = ev.CGEvent()
            CGEventPost(kCGHIDEventTap, cg)

    async def media_key(self, key: str) -> Dict[str, Any]:
        k = str(key or "").strip().lower()
        code = self.KEY_MAP.get(k)
        if code is None:
            raise RuntimeError("不支持的媒体键操作")

        await asyncio.to_thread(self._post_media_key_sync, code)
        return {"message": f"已执行媒体键操作：{k}"}

    async def set_volume_delta(self, delta: int) -> Dict[str, Any]:
        # Best-effort: change system output volume.
        d = int(delta)
        if d == 0:
            return {"message": "音量未变化"}

        script = (
            "set v to output volume of (get volume settings)\n"
            f"set nv to v + ({d})\n"
            "if nv > 100 then set nv to 100\n"
            "if nv < 0 then set nv to 0\n"
            "set volume output volume nv\n"
            "return nv"
        )
        nv = self._osascript(script)
        return {"message": f"已设置系统音量：{nv}"}

    async def get_now_playing(self) -> Dict[str, Any]:
        """Best-effort now playing.

        We try Apple Music then Spotify. System-wide session is not reliably accessible.
        """

        # Apple Music
        try:
            name = self._osascript('tell application "Music" to get name of current track')
            artist = self._osascript('tell application "Music" to get artist of current track')
            state = self._osascript('tell application "Music" to get player state')
            if name:
                return {"app": "Music", "track": name, "artist": artist, "state": state}
        except Exception:
            pass

        # Spotify
        try:
            name = self._osascript('tell application "Spotify" to get name of current track')
            artist = self._osascript('tell application "Spotify" to get artist of current track')
            state = self._osascript('tell application "Spotify" to get player state')
            if name:
                return {"app": "Spotify", "track": name, "artist": artist, "state": state}
        except Exception:
            pass

        return {"app": None, "track": None, "artist": None, "state": None, "message": "无法获取当前曲目信息"}
