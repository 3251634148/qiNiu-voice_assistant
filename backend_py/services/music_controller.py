from __future__ import annotations

import platform
import subprocess
from typing import Any, Dict, Optional


class MusicController:
    """Music control via AppleScript on macOS.

    Stage-2 goal is protocol parity. We keep the implementation minimal and strategy-friendly.
    """

    def __init__(self) -> None:
        self.system = platform.system().lower()

    def _osascript(self, script: str) -> str:
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "osascript 执行失败")
        return proc.stdout.strip()

    async def stop_music(self) -> Dict[str, Any]:
        if self.system != "darwin":
            raise RuntimeError("当前平台暂不支持停止音乐")

        # Try Apple Music first, then Spotify.
        try:
            self._osascript('tell application "Music" to pause')
            return {"message": "已暂停 Apple Music"}
        except Exception:
            pass

        self._osascript('tell application "Spotify" to pause')
        return {"message": "已暂停 Spotify"}

    async def play_music(self, *, source: Optional[str] = None, query: Optional[str] = None) -> Dict[str, Any]:
        if self.system != "darwin":
            raise RuntimeError("当前平台暂不支持播放音乐")

        # Minimal behavior: if app is running, resume. Query-based search is left for stage-3.
        if source == "spotify":
            self._osascript('tell application "Spotify" to play')
            return {"message": "已开始播放 Spotify"}

        if source == "apple":
            self._osascript('tell application "Music" to play')
            return {"message": "已开始播放 Apple Music"}

        # auto: try Apple Music then Spotify
        try:
            self._osascript('tell application "Music" to play')
            return {"message": "已开始播放 Apple Music"}
        except Exception:
            self._osascript('tell application "Spotify" to play')
            return {"message": "已开始播放 Spotify"}
