from __future__ import annotations

import asyncio
import difflib
import json
import os
import platform
import random
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _pbcopy(text: str) -> None:
    """Write text to clipboard.

    We prefer paste for Chinese queries to avoid IME-related popups.
    """

    subprocess.run(["pbcopy"], input=str(text), text=True, check=False)


def _sips_bmp_bytes(image_path: str, *, max_size: int = 96) -> bytes:
    """Render an image to BMP bytes via macOS `sips`.

    We intentionally avoid adding third-party deps (Pillow). `sips` can reliably output BMP.
    """

    src = str(image_path or "").strip()
    if not src:
        return b""

    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".bmp", delete=False) as f:
            tmp_path = f.name

        # `-Z` keeps aspect ratio and constrains the largest dimension.
        proc = subprocess.run(
            [
                "sips",
                "-Z",
                str(int(max_size)),
                "-s",
                "format",
                "bmp",
                src,
                "--out",
                tmp_path,
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return b""

        try:
            with open(tmp_path, "rb") as rf:
                return rf.read()
        except Exception:
            return b""
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def _bmp_ahash(
    bmp_bytes: bytes,
    *,
    crop_left: float = 0.15,
    crop_right: float = 0.98,
    crop_top: float = 0.08,
    crop_bottom: float = 0.86,
    grid: int = 16,
) -> str:
    """Compute a simple average-hash (aHash) from a BMP image.

    We hash a cropped central region to reduce sensitivity to:
    - Left sidebar highlight
    - Top search bar cursor blink
    - Bottom playback progress updates
    """

    if not bmp_bytes or len(bmp_bytes) < 54:
        return ""

    if bmp_bytes[0:2] != b"BM":
        return ""

    pixel_offset = int.from_bytes(bmp_bytes[10:14], "little", signed=False)
    dib_size = int.from_bytes(bmp_bytes[14:18], "little", signed=False)
    if dib_size < 40 or pixel_offset <= 0:
        return ""

    width = int.from_bytes(bmp_bytes[18:22], "little", signed=True)
    height = int.from_bytes(bmp_bytes[22:26], "little", signed=True)
    planes = int.from_bytes(bmp_bytes[26:28], "little", signed=False)
    bpp = int.from_bytes(bmp_bytes[28:30], "little", signed=False)
    compression = int.from_bytes(bmp_bytes[30:34], "little", signed=False)

    if planes != 1 or bpp not in (24, 32):
        return ""

    # `sips` often outputs 32bpp with BI_BITFIELDS (compression=3).
    if bpp == 24 and compression != 0:
        return ""
    if bpp == 32 and compression not in (0, 3):
        return ""

    abs_w = abs(int(width))
    abs_h = abs(int(height))
    if abs_w <= 0 or abs_h <= 0:
        return ""

    top_down = height < 0
    bytes_per_pixel = bpp // 8
    row_stride = ((abs_w * bytes_per_pixel + 3) // 4) * 4

    pixel_data = bmp_bytes[pixel_offset:]
    if len(pixel_data) < row_stride * abs_h:
        return ""

    left = max(0, min(abs_w - 1, int(abs_w * crop_left)))
    right = max(left + 1, min(abs_w, int(abs_w * crop_right)))
    top = max(0, min(abs_h - 1, int(abs_h * crop_top)))
    bottom = max(top + 1, min(abs_h, int(abs_h * crop_bottom)))

    crop_w = right - left
    crop_h = bottom - top
    if crop_w < grid or crop_h < grid:
        return ""

    block_w = crop_w / float(grid)
    block_h = crop_h / float(grid)

    blocks: list[int] = []
    blocks_sum = 0

    def _get_lum(xx: int, yy: int) -> int:
        row = yy if top_down else (abs_h - 1 - yy)
        base = row * row_stride + xx * bytes_per_pixel
        b = pixel_data[base]
        g = pixel_data[base + 1]
        r = pixel_data[base + 2]
        return int((r * 3 + g * 6 + b) // 10)

    for gy in range(grid):
        y0 = top + int(gy * block_h)
        y1 = top + int((gy + 1) * block_h)
        if y1 <= y0:
            y1 = y0 + 1
        if y1 > bottom:
            y1 = bottom

        for gx in range(grid):
            x0 = left + int(gx * block_w)
            x1 = left + int((gx + 1) * block_w)
            if x1 <= x0:
                x1 = x0 + 1
            if x1 > right:
                x1 = right

            lum_sum = 0
            count = 0
            for yy in range(y0, y1):
                for xx in range(x0, x1):
                    lum_sum += _get_lum(xx, yy)
                    count += 1

            v = lum_sum // max(1, count)
            blocks.append(v)
            blocks_sum += v

    avg = blocks_sum / float(len(blocks) or 1)
    bits = 0
    for v in blocks:
        bits = (bits << 1) | (1 if v >= avg else 0)

    hex_len = (grid * grid + 3) // 4
    return format(bits, f"0{hex_len}x")


def _phash_ahash(image_path: str) -> str:
    bmp = _sips_bmp_bytes(image_path, max_size=96)
    return _bmp_ahash(bmp)


def _hamming_distance_hex(a: str, b: str) -> int:
    if not a or not b or len(a) != len(b):
        return 10**9
    try:
        x = int(a, 16) ^ int(b, 16)
        return x.bit_count()
    except Exception:
        return 10**9


from backend_py.services.macos_media_control import MacOSMediaControl
from backend_py.services.macos_ui_automation import MacOSUIAutomation


KUGOU_APP_NAMES = ["酷狗音乐", "KugouMusic", "Kugou Music"]


def _compose_roi(
    base: tuple[float, float, float, float],
    sub: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Compose a child ROI inside a base ROI (normalized coords, top-left origin)."""
    bx, by, bw, bh = base
    sx, sy, sw, sh = sub
    return (bx + sx * bw, by + sy * bh, bw * sw, bh * sh)


# Normalized ROIs (origin top-left) for KuGou window screenshots.
KUGOU_ROIS: dict[str, tuple[float, float, float, float]] = {
    "full": (0.0, 0.0, 1.0, 1.0),
    # Keep sidebar ROI tight to avoid matching content-area words like "热门的音乐".
    "sidebar": (0.0, 0.18, 0.12, 0.72),
    # Sub-ROI inside sidebar for the top "音乐" entry (relative to sidebar ROI).
    "sidebar_music_sub": (0.0, 0.08, 1.0, 0.38),
    # Compose a tighter ROI for clicking the sidebar "音乐" entry; avoid hitting "视频" below.
    "sidebar_music": _compose_roi((0.0, 0.18, 0.12, 0.72), (0.0, 0.08, 1.0, 0.38)),
    # NOTE: Keep this ROI stable for click geometry.
    "top_search": (0.18, 0.00, 0.78, 0.16),
    # A wider ROI dedicated for search-view verification/state detection.
    # - Extend to the right edge to include the top-right "取消" button.
    # - Extend slightly to the left to include "历史搜索" which may be left of the search bar.
    "top_search_verify": (0.12, 0.00, 0.88, 0.20),
    # A tighter ROI around the search bar text area (top-right).
    "search_bar": (0.60, 0.00, 0.36, 0.12),
    # Tabs/controls are not always in a single row across versions/themes.
    # Use a taller ROI to cover both the top nav (音乐/艺人/动态) and the mid tabs (歌单/音频/歌手/专辑/视频).
    "tabs": (0.18, 0.04, 0.78, 0.40),
    "result_list_top": (0.18, 0.20, 0.78, 0.45),
    "bottom_player": (0.00, 0.86, 1.00, 0.14),
}

# KuGou OCR preset derived from `test_scripts/debug_ocr_vision_kugou.py` best-case runs.
# NOTE: `accurate=False` is surprisingly better for small UI fonts here.
KUGOU_OCR_CLICK_KWARGS: dict[str, Any] = {
    "ocr_scale": 1.0,
    "ocr_grayscale": True,
    "ocr_accurate": False,
    "ocr_language_correction": True,
    "ocr_languages": ["zh-Hans", "zh-Hant", "en-US"],
    "ocr_custom_words": [
        "推荐",
        "频道",
        "歌单",
        "歌手",
        "音乐",
        "视频",
        "我的",
        "搜索",
        "取消",
        "历史搜索",
        "收藏",
        "我喜欢",
        "喜欢",
        "单曲",
        "歌曲",
        "综合",
        "播放",
        "暂停",
    ],
}


class MusicController:
    """Music control utilities.

    - For Apple Music/Spotify: prefer AppleScript (stable)
    - For apps without usable API keys (e.g. KuGou): use UI automation (OCR + click)

    Notes:
    - UI automation is inherently brittle and requires Accessibility permission.
    """

    def __init__(self) -> None:
        self.system = platform.system().lower()
        self.ui = MacOSUIAutomation() if self.system == "darwin" else None

    def _osascript(self, script: str) -> str:
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "osascript 执行失败")
        return proc.stdout.strip()

    @staticmethod
    def _escape_applescript_string(value: str) -> str:
        return str(value).replace("\\", "\\\\").replace('"', '\\"')

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
        """Legacy music play entry (low-risk).

        This stays conservative: only Apple Music/Spotify.
        UI automation flows should go through `music_ui` tool.
        """

        if self.system != "darwin":
            raise RuntimeError("当前平台暂不支持播放音乐")

        src = str(source or "").strip().lower() or None
        query_text = str(query or "").strip()

        # KuGou: low-risk default = open app + system media key.
        if src == "kugou":
            for name in KUGOU_APP_NAMES:
                try:
                    subprocess.run(["open", "-a", name], capture_output=True, text=True)
                    break
                except Exception:
                    continue

            media = MacOSMediaControl()
            await media.media_key("play_pause")
            return {"message": "已打开酷狗并触发播放/暂停"}

        # Apple Music: if query is provided, treat it as a playlist name.
        if src == "apple" and query_text:
            playlist = self._escape_applescript_string(query_text)
            self._osascript(f'tell application "Music" to play playlist "{playlist}"')
            return {"message": f"已开始播放 Apple Music 歌单：{query_text}"}

        if src == "spotify":
            self._osascript('tell application "Spotify" to play')
            return {"message": "已开始播放 Spotify"}

        if src == "apple":
            self._osascript('tell application "Music" to play')
            return {"message": "已开始播放 Apple Music"}

        # auto: try Apple Music playlist(if query), then Apple Music play, then Spotify.
        if query_text:
            try:
                playlist = self._escape_applescript_string(query_text)
                self._osascript(f'tell application "Music" to play playlist "{playlist}"')
                return {"message": f"已开始播放 Apple Music 歌单：{query_text}"}
            except Exception:
                pass

        try:
            self._osascript('tell application "Music" to play')
            return {"message": "已开始播放 Apple Music"}
        except Exception:
            self._osascript('tell application "Spotify" to play')
            return {"message": "已开始播放 Spotify"}

    async def music_ui(
        self,
        *,
        player: str,
        action: str,
        query: Optional[str] = None,
        debug: bool = False,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Control music apps through UI automation.

        Args:
            player: kugou | apple_music
            action: random_favorites | playlist | search
            query: playlist/search keyword
        """

        if self.system != "darwin" or self.ui is None:
            raise RuntimeError("当前平台暂不支持 UI 自动化音乐控制")

        await self.ui.ensure_accessibility_ready()

        p = str(player or "").strip().lower()
        a = str(action or "").strip().lower()
        q = str(query or "").strip()

        if p not in {"kugou", "apple_music"}:
            raise RuntimeError("player 仅支持 kugou / apple_music")
        if a not in {"random_favorites", "favorites_first", "playlist", "search"}:
            raise RuntimeError("action 仅支持 random_favorites / favorites_first / playlist / search")

        if p == "apple_music":
            return await self._apple_music_ui(action=a, query=q, debug=debug, dry_run=dry_run)

        return await self._kugou_ui(action=a, query=q, debug=debug, dry_run=dry_run)

    async def _apple_music_ui(self, *, action: str, query: str, debug: bool, dry_run: bool) -> Dict[str, Any]:
        # Playlist play is stable via AppleScript.
        if action == "playlist":
            if not query:
                raise RuntimeError("播放歌单需要提供 query")
            if dry_run:
                return {"message": f"(dry-run) 将播放 Apple Music 歌单：{query}"}
            result = await self.play_music(source="apple", query=query)
            return {"message": result.get("message", "已开始播放 Apple Music"), "debug": {"mode": "applescript"} if debug else None}

        if action == "search":
            if not query:
                raise RuntimeError("搜索播放需要提供 query")
            if dry_run:
                return {"message": f"(dry-run) 将在 Apple Music 搜索并播放：{query}"}

            # Best-effort AppleScript search; fall back to UI hotkeys.
            escaped = self._escape_applescript_string(query)
            script = (
                'tell application "Music"\n'
                f'  set theResults to (search library playlist 1 for "{escaped}")\n'
                '  if theResults is {} then return "NOT_FOUND"\n'
                '  set t to item 1 of theResults\n'
                '  play t\n'
                '  return name of t\n'
                'end tell'
            )
            try:
                name = self._osascript(script)
                if name.strip() == "NOT_FOUND":
                    raise RuntimeError("未找到匹配曲目")
                return {"message": f"已在 Apple Music 播放：{name}", "debug": {"mode": "applescript_search"} if debug else None}
            except Exception:
                await self.ui.activate_app("Music")
                await self.ui.hotkey("f", modifiers=["command down"])
                await self.ui.type_text(query)
                await self.ui.key_code(36)
                return {"message": "已在 Apple Music 发起搜索，请确认是否开始播放", "debug": {"mode": "ui_fallback"} if debug else None}

        raise RuntimeError("Apple Music 当前不支持该 action")

    async def _kugou_ui(self, *, action: str, query: str, debug: bool, dry_run: bool) -> Dict[str, Any]:
        await self._open_and_activate_kugou()
        await asyncio.sleep(0.6)

        debug_info: dict[str, Any] = {}

        def _pos_key(box: Any) -> tuple[float, float]:
            y = getattr(box, "y", None)
            x = getattr(box, "x", None)
            try:
                yv = float(y)
            except Exception:
                yv = 10**9
            try:
                xv = float(x)
            except Exception:
                xv = 10**9
            return (yv, xv)

        if action == "random_favorites":
            # Navigate to favorites
            try:
                await self._best_effort_click_any(
                    ["我的", "我 的"],
                    tag="kugou_my",
                    dry_run=dry_run,
                    debug=debug_info,
                    click_text_kwargs={"roi": KUGOU_ROIS["full"], **KUGOU_OCR_CLICK_KWARGS},
                )
                await asyncio.sleep(0.3)
                await self._best_effort_click_any(
                    ["收藏", "我喜欢", "喜欢"],
                    tag="kugou_fav",
                    dry_run=dry_run,
                    debug=debug_info,
                    click_text_kwargs={"roi": KUGOU_ROIS["full"], **KUGOU_OCR_CLICK_KWARGS},
                )
                await asyncio.sleep(0.8)
                debug_info["mode"] = "ocr_first"
            except Exception as e:
                debug_info.setdefault("warnings", []).append(f"favorites OCR-first 失败，回退坐标兜底：{e}")
                debug_info["mode"] = "fallback_coordinates"

                # Fallback: click sidebar '我的' by window-relative points.
                base_capture = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_fav_base")
                debug_info.setdefault("captures", []).append({"step": "fav_base", "capture": base_capture})
                wb = (base_capture or {}).get("windowBounds") or {}
                bx = float(wb.get("x") or 0.0)
                by = float(wb.get("y") or 0.0)
                bw = float(wb.get("width") or 0.0)
                bh = float(wb.get("height") or 0.0)
                if bw <= 1 or bh <= 1:
                    raise RuntimeError("无法获取酷狗窗口尺寸，无法执行收藏播放")

                def _point(x_ratio: float, y_ratio_from_top: float) -> dict[str, float]:
                    xr = max(0.0, min(1.0, float(x_ratio)))
                    yr = max(0.0, min(1.0, float(y_ratio_from_top)))
                    return {"x": bx + bw * xr, "y": by + bh * yr}

                nav_my_points = [
                    (0.055, 0.70),
                    (0.055, 0.73),
                    (0.055, 0.67),
                ]
                for idx, (xr, yr) in enumerate(nav_my_points):
                    pt = _point(xr, yr)
                    await self.ui.click_at(pt["x"], pt["y"], clicks=1)
                    debug_info.setdefault("clicks", []).append({"step": "nav_my", "idx": idx, "point": pt})
                    await asyncio.sleep(0.25)

                # Try entering favorites via OCR again after coordinate nav.
                await self._best_effort_click_any(
                    ["收藏", "我喜欢", "喜欢"],
                    tag="kugou_fav_after_nav",
                    dry_run=dry_run,
                    debug=debug_info,
                    click_text_kwargs={"roi": KUGOU_ROIS["full"], **KUGOU_OCR_CLICK_KWARGS},
                    allow_fail=True,
                )
                await asyncio.sleep(0.8)

            if dry_run:
                return {"message": "(dry-run) 将从酷狗收藏/喜欢列表随机选择并播放", "debug": debug_info if debug else None}

            capture = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_list")
            debug_info["windowCapture"] = capture

            boxes = await self.ui.ocr_screenshot_advanced(
                str(capture.get("screenshotPath") or ""),
                roi=KUGOU_ROIS["full"],
                scale=1.0,
                grayscale=True,
                accurate=False,
                custom_words=KUGOU_OCR_CLICK_KWARGS.get("ocr_custom_words"),
            )
            image_h = float(((capture.get("imageSize") or {}).get("height")) or 0.0)
            candidates = self._pick_song_like_boxes(boxes, image_height=image_h)
            if not candidates:
                # Fallback: if OCR cannot extract list items, click a random row area to attempt playback.
                debug_info.setdefault("warnings", []).append("未能从列表 OCR 识别到可播放项，改用坐标随机点选兜底")

                image_w = float(((capture.get("imageSize") or {}).get("width")) or 0.0)
                image_h = float(((capture.get("imageSize") or {}).get("height")) or 0.0)
                if image_w <= 1 or image_h <= 1:
                    raise RuntimeError("无法获取截图尺寸，无法执行坐标兜底")

                x_px = image_w * 0.30
                y_px = image_h * random.uniform(0.30, 0.80)
                sx, sy = self.ui._to_screen_point_from_window_image_point(  # noqa: SLF001
                    x_px,
                    y_px,
                    window_bounds=capture.get("windowBounds") or {},
                    image_size=capture.get("imageSize") or {},
                )
                await self.ui.click_at(sx, sy, clicks=2)
                return {"message": "已在酷狗尝试从收藏列表随机播放（坐标兜底）", "debug": debug_info if debug else None}

            chosen = random.choice(candidates)
            cx, cy = chosen.center()
            sx, sy = self.ui._to_screen_point_from_window_image_point(  # noqa: SLF001
                cx,
                cy,
                window_bounds=capture.get("windowBounds") or {},
                image_size=capture.get("imageSize") or {},
            )
            await self.ui.click_at(sx, sy, clicks=2)

            return {"message": f"已在酷狗随机播放：{chosen.text}", "debug": debug_info if debug else None}

        if action == "favorites_first":
            # Navigate to favorites and play the first visible item.
            try:
                await self._best_effort_click_any(
                    ["我的", "我 的"],
                    tag="kugou_my",
                    dry_run=dry_run,
                    debug=debug_info,
                    click_text_kwargs={"roi": KUGOU_ROIS["full"], **KUGOU_OCR_CLICK_KWARGS},
                )
                await asyncio.sleep(0.3)
                await self._best_effort_click_any(
                    ["收藏", "我喜欢", "喜欢"],
                    tag="kugou_fav",
                    dry_run=dry_run,
                    debug=debug_info,
                    click_text_kwargs={"roi": KUGOU_ROIS["full"], **KUGOU_OCR_CLICK_KWARGS},
                )
                await asyncio.sleep(0.8)
                debug_info["mode"] = "ocr_first"
            except Exception as e:
                debug_info.setdefault("warnings", []).append(f"favorites_first OCR-first 失败，回退坐标兜底：{e}")
                debug_info["mode"] = "fallback_coordinates"

                base_capture = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_fav_first_base")
                debug_info.setdefault("captures", []).append({"step": "fav_first_base", "capture": base_capture})
                wb = (base_capture or {}).get("windowBounds") or {}
                bx = float(wb.get("x") or 0.0)
                by = float(wb.get("y") or 0.0)
                bw = float(wb.get("width") or 0.0)
                bh = float(wb.get("height") or 0.0)
                if bw <= 1 or bh <= 1:
                    raise RuntimeError("无法获取酷狗窗口尺寸，无法执行收藏播放")

                def _point(x_ratio: float, y_ratio_from_top: float) -> dict[str, float]:
                    xr = max(0.0, min(1.0, float(x_ratio)))
                    yr = max(0.0, min(1.0, float(y_ratio_from_top)))
                    return {"x": bx + bw * xr, "y": by + bh * yr}

                nav_my_points = [
                    (0.055, 0.70),
                    (0.055, 0.73),
                    (0.055, 0.67),
                ]
                for idx, (xr, yr) in enumerate(nav_my_points):
                    pt = _point(xr, yr)
                    await self.ui.click_at(pt["x"], pt["y"], clicks=1)
                    debug_info.setdefault("clicks", []).append({"step": "nav_my", "idx": idx, "point": pt})
                    await asyncio.sleep(0.25)

                await self._best_effort_click_any(
                    ["收藏", "我喜欢", "喜欢"],
                    tag="kugou_fav_after_nav",
                    dry_run=dry_run,
                    debug=debug_info,
                    click_text_kwargs={"roi": KUGOU_ROIS["full"], **KUGOU_OCR_CLICK_KWARGS},
                    allow_fail=True,
                )
                await asyncio.sleep(0.8)

            if dry_run:
                return {"message": "(dry-run) 将在酷狗打开我喜欢并播放第一首", "debug": debug_info if debug else None}

            capture = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_fav_first")
            debug_info["windowCapture"] = capture

            boxes = await self.ui.ocr_screenshot_advanced(
                str(capture.get("screenshotPath") or ""),
                roi=KUGOU_ROIS["full"],
                scale=1.0,
                grayscale=True,
                accurate=False,
                custom_words=KUGOU_OCR_CLICK_KWARGS.get("ocr_custom_words"),
            )
            image_h = float(((capture.get("imageSize") or {}).get("height")) or 0.0)
            candidates = self._pick_song_like_boxes(boxes, image_height=image_h)
            if not candidates:
                # Fallback: if OCR cannot extract list items, click the first row area to attempt playback.
                debug_info.setdefault("warnings", []).append("未能从列表 OCR 识别到可播放项，改用坐标点选第一首兜底")

                image_w = float(((capture.get("imageSize") or {}).get("width")) or 0.0)
                image_h = float(((capture.get("imageSize") or {}).get("height")) or 0.0)
                if image_w <= 1 or image_h <= 1:
                    raise RuntimeError("无法获取截图尺寸，无法执行坐标兜底")

                x_px = image_w * 0.30
                y_px = image_h * 0.32
                sx, sy = self.ui._to_screen_point_from_window_image_point(  # noqa: SLF001
                    x_px,
                    y_px,
                    window_bounds=capture.get("windowBounds") or {},
                    image_size=capture.get("imageSize") or {},
                )
                await self.ui.click_at(sx, sy, clicks=2)
                return {"message": "已在酷狗尝试播放我喜欢第一首（坐标兜底）", "debug": debug_info if debug else None}

            chosen = min(candidates, key=_pos_key)
            cx, cy = chosen.center()
            sx, sy = self.ui._to_screen_point_from_window_image_point(  # noqa: SLF001
                cx,
                cy,
                window_bounds=capture.get("windowBounds") or {},
                image_size=capture.get("imageSize") or {},
            )
            await self.ui.click_at(sx, sy, clicks=2)
            return {"message": f"已在酷狗播放第一首：{chosen.text}", "debug": debug_info if debug else None}

        if action == "playlist":
            if not query:
                raise RuntimeError("playlist 需要提供 query")

            # NOTE: KuGou does not map Cmd+F to search focus. Prefer menu/relative click instead of OCR.
            try:
                debug_info["frontmostProcess"] = await self.ui.get_frontmost_process_name()
            except Exception:
                pass

            before_capture = None
            if not dry_run:
                before_capture = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_playlist_before")
                debug_info["beforeCapture"] = before_capture

            # Attempt to enter search mode via menu (best-effort) then click near the top search box area.
            # Do NOT rely on "frontmost" here, as Electron may steal focus; target KuGou process explicitly.
            try:
                await self.ui.set_process_frontmost("酷狗音乐")
            except Exception as e:
                debug_info.setdefault("warnings", []).append(f"置前酷狗失败（忽略）：{e}")

            try:
                menu_res = await self.ui.click_process_menu_item_contains(
                    process_name="酷狗音乐",
                    keywords=["搜索", "查找", "全局搜索", "Search", "Find"],
                    tag="kugou_menu_search",
                    dry_run=dry_run,
                )
                debug_info.setdefault("menu", []).append(menu_res)
            except Exception as e:
                debug_info.setdefault("menu", []).append({"error": str(e)})

            focus_points = [
                (0.52, 0.055),
                (0.52, 0.070),
                (0.50, 0.055),
                (0.50, 0.085),
                (0.35, 0.070),
                (0.70, 0.070),
                (0.85, 0.070),
            ]
            for xr, yr in focus_points:
                try:
                    rel = await self.ui.click_window_relative(
                        owner_names=KUGOU_APP_NAMES,
                        x_ratio=xr,
                        y_ratio_from_top=yr,
                        clicks=1,
                    )
                    focus_info = None
                    try:
                        focus_info = await self.ui.get_focused_ui_element_info("酷狗音乐")
                    except Exception:
                        focus_info = None

                    debug_info.setdefault("focus", []).append(
                        {"method": "relative_click", "xRatio": xr, "yRatio": yr, "result": rel, "focused": focus_info}
                    )
                    if isinstance(focus_info, dict) and focus_info.get("ok") is True:
                        role = str(focus_info.get("role") or "")
                        if "Text" in role or "Field" in role:
                            break
                except Exception as e:
                    debug_info.setdefault("focus", []).append({"method": "relative_click", "xRatio": xr, "yRatio": yr, "error": str(e)})

            await asyncio.sleep(0.2)

            # Clear existing text then type query.
            try:
                await self.ui.hotkey("a", modifiers=["command down"])
                await self.ui.key_code(51)
            except Exception as e:
                debug_info.setdefault("warnings", []).append(f"清空输入框失败（忽略）：{e}")

            await self.ui.type_text(query)
            await self.ui.key_code(36)
            await asyncio.sleep(0.7)

            # Best-effort: switch to playlist tab (may fail due to self-drawn UI / OCR issues).
            await self._best_effort_click_any(
                ["歌单", "歌 单"],
                tag="kugou_tab_playlist",
                dry_run=dry_run,
                debug=debug_info,
                click_text_kwargs={"roi": KUGOU_ROIS["full"], **KUGOU_OCR_CLICK_KWARGS},
                allow_fail=True,
            )
            await asyncio.sleep(0.4)

            # Try to play the first result via keyboard navigation.
            await self.ui.key_code(125)
            await asyncio.sleep(0.12)
            await self.ui.key_code(36)
            await asyncio.sleep(0.7)

            if dry_run:
                return {"message": f"(dry-run) 将在酷狗搜索并尝试播放第一首：{query}", "debug": debug_info if debug else None}

            after_capture = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_playlist_after")
            debug_info["afterCapture"] = after_capture

            before_path = str((before_capture or {}).get("screenshotPath") or "")
            after_path = str(after_capture.get("screenshotPath") or "")
            if before_path and after_path:
                before_hash = self.ui.file_sha256(before_path)
                after_hash = self.ui.file_sha256(after_path)
                debug_info["uiChange"] = {"before": before_hash, "after": after_hash, "same": before_hash == after_hash}
                if before_hash and after_hash and before_hash == after_hash:
                    raise RuntimeError(
                        "已执行酷狗搜索/播放流程，但界面未发生变化，判定未进入搜索或未触发播放。"
                        f"已导出前后截图：{before_path} / {after_path}"
                    )

            return {"message": f"已在酷狗搜索并尝试播放第一首：{query}", "debug": debug_info if debug else None}

        if action == "search":
            if not query:
                raise RuntimeError("search 需要提供 query")

            # Refactored: pure OCR workflow (no coordinate/keyboard fallback for navigation).
            return await self._kugou_search_ocr_workflow(query=query, debug=debug, dry_run=dry_run)

            ocr_min_confidence = 0.75

            # ROIs (normalized, origin top-left)
            rois = {
                "full": (0.0, 0.0, 1.0, 1.0),
                "sidebar": (0.0, 0.18, 0.22, 0.72),
                "top_search": (0.18, 0.00, 0.78, 0.16),
                "tabs": (0.18, 0.12, 0.78, 0.16),
                "result_list_top": (0.18, 0.20, 0.78, 0.45),
                "bottom_player": (0.00, 0.86, 1.00, 0.14),
            }

            async def _run_coordinate_fallback() -> Dict[str, Any]:
                debug_info["mode"] = "fallback_coordinates"

                # NOTE:
                # - KuGou does not map Cmd+F to search focus (verified by user).
                # - Typing Chinese via System Events may trigger IME selection popups.
                # - For reliability: always go home first, then click by window bounds + paste query.
                try:
                    debug_info["frontmostProcess"] = await self.ui.get_frontmost_process_name()
                except Exception:
                    pass

                # Do NOT rely on "frontmost" here, as Electron may steal focus; target KuGou process explicitly.
                try:
                    await self.ui.set_process_frontmost("酷狗音乐")
                except Exception as e:
                    debug_info.setdefault("warnings", []).append(f"置前酷狗失败（忽略）：{e}")

                # Always go back to home first (user requirement).
                # KuGou may start on a song detail page; use multiple best-effort keys.
                key_attempts = [
                    {"name": "escape", "fn": lambda: self.ui.key_code(53)},
                    {"name": "cmd_left_bracket", "fn": lambda: self.ui.hotkey("[", modifiers=["command down"])},
                    {"name": "cmd_1", "fn": lambda: self.ui.hotkey("1", modifiers=["command down"])},
                ]
                for item in key_attempts:
                    entry: dict[str, Any] = {"step": "go_home_key", "name": str(item.get("name") or ""), "ok": True}
                    try:
                        await item["fn"]()  # type: ignore[index]
                    except Exception as e:
                        entry["ok"] = False
                        entry["error"] = str(e)
                    debug_info.setdefault("goHome", []).append(entry)
                    await asyncio.sleep(0.35)

                before_capture = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_search_home")
                debug_info["beforeCapture"] = before_capture

                # Window-bounds based clicks.
                # IMPORTANT: On some macOS setups `kCGWindowBounds.Y` behaves like a top-origin value.
                # Our debug runs confirm KuGou search is reliable with: y = bounds.y + bounds.height * y_ratio_from_top.
                window_bounds = (before_capture or {}).get("windowBounds") or {}
                bx = float(window_bounds.get("x") or 0.0)
                by = float(window_bounds.get("y") or 0.0)
                bw = float(window_bounds.get("width") or 0.0)
                bh = float(window_bounds.get("height") or 0.0)
                if bw <= 1 or bh <= 1:
                    raise RuntimeError("无法获取酷狗窗口尺寸，无法执行搜索")

                def _point(x_ratio: float, y_ratio_from_top: float) -> dict[str, float]:
                    xr = max(0.0, min(1.0, float(x_ratio)))
                    yr = max(0.0, min(1.0, float(y_ratio_from_top)))
                    sx = bx + bw * xr
                    sy = by + bh * yr
                    return {"x": sx, "y": sy}

                def _norm(value: str) -> str:
                    v = str(value or "")
                    v = "".join(v.split())
                    v = v.replace("\uffff", "").replace("\ufffd", "")
                    return v.strip().lower()

                def _target_song_from_query(q: str) -> str:
                    parts = [p for p in str(q or "").split() if p]
                    if len(parts) >= 2:
                        return " ".join(parts[1:]).strip()
                    return str(q or "").strip()

                def _roi_px(roi: tuple[float, float, float, float], *, iw: float, ih: float) -> tuple[float, float, float, float]:
                    rx, ry, rw, rh = roi
                    return (float(rx) * iw, float(ry) * ih, float(rw) * iw, float(rh) * ih)

                def _box_center_in_roi(box: Any, *, roi_px: tuple[float, float, float, float]) -> bool:
                    x0, y0, w0, h0 = roi_px
                    x1 = x0 + w0
                    y1 = y0 + h0
                    try:
                        cx, cy = box.center()
                    except Exception:
                        return False
                    return (x0 <= float(cx) <= x1) and (y0 <= float(cy) <= y1)

                def _has_text_in_roi(
                    boxes: List[Any],
                    *,
                    target: str,
                    roi_px: tuple[float, float, float, float],
                    min_conf: float,
                ) -> bool:
                    t_norm = _norm(target)
                    if not t_norm:
                        return False

                    for b in boxes:
                        try:
                            conf = float(getattr(b, "confidence", 0.0) or 0.0)
                        except Exception:
                            conf = 0.0
                        if conf < float(min_conf):
                            continue

                        if not _box_center_in_roi(b, roi_px=roi_px):
                            continue

                        text_norm = _norm(str(getattr(b, "text", "") or ""))
                        if not text_norm:
                            continue

                        if t_norm in text_norm:
                            return True

                    return False

                def _parse_mmss(text: str) -> Optional[int]:
                    m = re.search(r"(\d{1,2}):(\d{2})", str(text or ""))
                    if not m:
                        return None
                    try:
                        mm = int(m.group(1))
                        ss = int(m.group(2))
                        return mm * 60 + ss
                    except Exception:
                        return None

                async def _detect_results_page_from_path(path: str) -> Dict[str, Any]:
                    boxes = await self.ui.ocr_screenshot_advanced(
                        path,
                        roi=KUGOU_ROIS["full"],
                        scale=1.0,
                        grayscale=True,
                        accurate=False,
                        custom_words=KUGOU_OCR_CLICK_KWARGS.get("ocr_custom_words"),
                    )

                    # Tabs that only appear after search results load.
                    tabs_candidates = ["综合", "单曲", "视频", "歌单", "听书", "专辑", "歌词"]
                    iw = float(((before_capture.get("imageSize") or {}).get("width")) or 0.0)
                    ih = float(((before_capture.get("imageSize") or {}).get("height")) or 0.0)
                    if float(iw) <= 1 or float(ih) <= 1:
                        return {"ok": False, "reason": "missing_image_size"}

                    roi_top = _roi_px(rois["top_search"], iw=iw, ih=ih)
                    roi_tabs = _roi_px(rois["tabs"], iw=iw, ih=ih)

                    cancel_ok = _has_text_in_roi(boxes, target="取消", roi_px=roi_top, min_conf=ocr_min_confidence)
                    tab_hits = 0
                    for t in tabs_candidates:
                        if _has_text_in_roi(boxes, target=t, roi_px=roi_tabs, min_conf=ocr_min_confidence):
                            tab_hits += 1

                    ratio = float(tab_hits) / float(len(tabs_candidates) or 1)
                    ok = bool(cancel_ok and ratio >= 0.5)
                    return {
                        "ok": ok,
                        "cancel": bool(cancel_ok),
                        "tabHits": int(tab_hits),
                        "tabCount": int(len(tabs_candidates)),
                        "tabRatio": ratio,
                    }

                async def _check_playback(*, tag_prefix: str) -> Dict[str, Any]:
                    target_song = _target_song_from_query(query)
                    target_norm = _norm(target_song)

                    captures = []
                    texts = []
                    seconds = []
                    bottom_hashes = []
                    for idx in range(2):
                        cap = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=f"{tag_prefix}_{idx}")
                        captures.append(cap)
                        path = str(cap.get("screenshotPath") or "")
                        if not path:
                            texts.append("")
                            seconds.append(None)
                            bottom_hashes.append("")
                            continue

                        boxes = await self.ui.ocr_screenshot_advanced(
                            path,
                            roi=KUGOU_ROIS["bottom_player"],
                            scale=1.0,
                            grayscale=True,
                            accurate=False,
                            custom_words=KUGOU_OCR_CLICK_KWARGS.get("ocr_custom_words"),
                        )
                        # Keep a compact bottom-area hash as a weak fallback for progress detection.
                        bmp = _sips_bmp_bytes(path, max_size=96)
                        bottom_hashes.append(_bmp_ahash(bmp, crop_left=0.0, crop_right=1.0, crop_top=0.86, crop_bottom=0.99))

                        items = []
                        for b in boxes:
                            txt = str(getattr(b, "text", "") or "").strip()
                            if txt:
                                items.append(txt)
                        joined = " ".join(items)
                        texts.append(joined)
                        seconds.append(_parse_mmss(joined))

                        if idx == 0:
                            await asyncio.sleep(1.0)

                    text0 = str(texts[0] if len(texts) > 0 else "")
                    text1 = str(texts[1] if len(texts) > 1 else "")
                    n0 = _norm(text0)
                    n1 = _norm(text1)

                    sim0 = difflib.SequenceMatcher(None, target_norm, n0).ratio() if target_norm and n0 else 0.0
                    sim1 = difflib.SequenceMatcher(None, target_norm, n1).ratio() if target_norm and n1 else 0.0
                    match_ok = bool((target_norm and target_norm in n0) or (target_norm and target_norm in n1) or (max(sim0, sim1) >= 0.6))

                    t0 = seconds[0] if len(seconds) > 0 else None
                    t1 = seconds[1] if len(seconds) > 1 else None
                    progress_ok = bool((t0 is not None) and (t1 is not None) and (int(t1) > int(t0)))

                    # Weak fallback: bottom hash changes with progress.
                    h0 = bottom_hashes[0] if len(bottom_hashes) > 0 else ""
                    h1 = bottom_hashes[1] if len(bottom_hashes) > 1 else ""
                    hash_delta = _hamming_distance_hex(h0, h1) if h0 and h1 else 10**9
                    hash_progress_ok = bool(hash_delta != 10**9 and hash_delta >= 6)

                    confirmed = bool(match_ok and (progress_ok or hash_progress_ok))
                    payload = {
                        "targetSong": target_song,
                        "targetNorm": target_norm,
                        "confirmed": confirmed,
                        "matchOk": match_ok,
                        "progressOk": progress_ok,
                        "hashProgressOk": hash_progress_ok,
                        "hashDelta": hash_delta,
                        "samples": [
                            {
                                "text": text0,
                                "seconds": t0,
                                "capture": captures[0],
                            },
                            {
                                "text": text1,
                                "seconds": t1,
                                "capture": captures[1],
                            },
                        ],
                    }
                    debug_info.setdefault("playbackCheck", []).append(payload)
                    return payload

                # IMPORTANT: To enter search, KuGou must be in the "音乐" page (not "我的").
                # OCR is unreliable on the "我的" page (small sidebar fonts), so use a few stable sidebar points.
                nav_music_points = [
                    (0.055, 0.42),
                    (0.055, 0.40),
                    (0.055, 0.44),
                ]
                for idx, (xr, yr) in enumerate(nav_music_points):
                    pt = _point(xr, yr)
                    await self.ui.click_at(pt["x"], pt["y"], clicks=1)
                    debug_info.setdefault("clicks", []).append({"step": "nav_music", "idx": idx, "point": pt})
                    await asyncio.sleep(0.25)

                nav_capture = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_after_nav_music")
                debug_info.setdefault("captures", []).append({"step": "after_nav_music", "capture": nav_capture})

                # Early stop: if we are already playing the target song, do not re-search.
                if not dry_run:
                    try:
                        pb = await _check_playback(tag_prefix="kugou_play_check_pre")
                        if bool((pb or {}).get("confirmed")) is True:
                            return {
                                "message": f"已在酷狗播放：{(pb or {}).get('targetSong')}",
                                "debug": debug_info if debug else None,
                            }
                    except Exception as e:
                        debug_info.setdefault("warnings", []).append(f"播放确认预检查失败（忽略）：{e}")

                # IMPORTANT:
                # Clicking menu items is risky here because matching may hit unrelated paths (e.g. recent items)
                # and can accidentally navigate away from KuGou.
                debug_info.setdefault("menu", []).append({"skipped": True, "reason": "avoid side-effects"})

                # Enter search and ensure we are on the search results page.
                # Different KuGou home layouts place the search box slightly differently, so we try multiple points.
                focus_points = [
                    (0.52, 0.055),
                    (0.52, 0.070),
                    (0.50, 0.055),
                    (0.50, 0.085),
                    (0.35, 0.070),
                    (0.70, 0.070),
                    (0.85, 0.070),
                    (0.72, 0.070),
                ]

                # Detect search page transition using perceptual hash.
                # NOTE: Raw sha256 is too sensitive to tiny UI changes (cursor blink/hover), causing false positives.
                nav_path = str(((nav_capture or {}).get("screenshotPath")) or "")
                nav_phash = _phash_ahash(nav_path) if nav_path else ""
                if nav_phash:
                    debug_info["navPHash"] = nav_phash

                # If we are already in a search view (e.g. previous run left KuGou on results),
                # the baseline may already equal a search page. Try clicking the top-right "取消" area once
                # to exit, then re-capture baseline. This keeps detection meaningful without OCR.
                cancel_threshold = 15
                try:
                    cancel_pt = _point(0.94, 0.07)
                    await self.ui.click_at(cancel_pt["x"], cancel_pt["y"], clicks=1)
                    debug_info.setdefault("clicks", []).append({"step": "cancel_try", "point": cancel_pt})
                    await asyncio.sleep(0.35)

                    cancel_capture = await self.ui.screenshot_window(
                        owner_names=KUGOU_APP_NAMES,
                        tag="kugou_after_cancel_try",
                    )
                    debug_info.setdefault("captures", []).append({"step": "after_cancel_try", "capture": cancel_capture})

                    cancel_path = str((cancel_capture or {}).get("screenshotPath") or "")
                    cancel_phash = _phash_ahash(cancel_path) if cancel_path else ""
                    cancel_dist = _hamming_distance_hex(nav_phash, cancel_phash)
                    debug_info["cancelTry"] = {
                        "path": cancel_path,
                        "phash": cancel_phash,
                        "dist": cancel_dist,
                        "threshold": cancel_threshold,
                    }

                    if cancel_phash and cancel_dist >= cancel_threshold:
                        nav_phash = cancel_phash
                        debug_info["navPHash"] = nav_phash
                except Exception as e:
                    debug_info.setdefault("warnings", []).append(f"cancel尝试失败（忽略）：{e}")

                phash_threshold = 20
                found_results = False
                last_attempt_capture: Optional[Dict[str, Any]] = None

                for idx, (xr, yr) in enumerate(focus_points):
                    enter_pt = _point(xr, yr)
                    await self.ui.click_at(enter_pt["x"], enter_pt["y"], clicks=1)
                    debug_info.setdefault("clicks", []).append({"step": "enter_search", "idx": idx, "point": enter_pt})
                    await asyncio.sleep(0.22)

                    # Clear existing text then paste query (avoid IME popups).
                    try:
                        await self.ui.hotkey("a", modifiers=["command down"])
                        await self.ui.key_code(51)
                    except Exception as e:
                        debug_info.setdefault("warnings", []).append(f"清空输入框失败（忽略）：{e}")

                    _pbcopy(query)
                    try:
                        await self.ui.hotkey("v", modifiers=["command down"])
                    except Exception as e:
                        debug_info.setdefault("warnings", []).append(f"粘贴搜索词失败（忽略）：{e}")

                    await asyncio.sleep(0.35)

                    # Prefer selecting the first dropdown suggestion, then enter to search.
                    await self.ui.key_code(125)  # Down
                    await asyncio.sleep(0.12)
                    await self.ui.key_code(36)  # Enter
                    await asyncio.sleep(0.85)

                    if dry_run:
                        found_results = True
                        break

                    attempt_capture = await self.ui.screenshot_window(
                        owner_names=KUGOU_APP_NAMES,
                        tag=f"kugou_search_attempt_{idx}",
                    )
                    last_attempt_capture = attempt_capture
                    debug_info.setdefault("captures", []).append({"step": "search_attempt", "idx": idx, "capture": attempt_capture})

                    cap_path = str(attempt_capture.get("screenshotPath") or "")
                    if not cap_path:
                        continue

                    # Prefer OCR-based result page detection: must see "取消" and >=50% of result tabs.
                    try:
                        results_info = await _detect_results_page_from_path(cap_path)
                        debug_info.setdefault("resultsPageDetect", []).append({"idx": idx, **(results_info or {})})
                        if bool((results_info or {}).get("ok")) is True:
                            found_results = True
                            break
                    except Exception as e:
                        debug_info.setdefault("warnings", []).append(f"结果页 OCR 判定失败（忽略，回退 pHash）：{e}")

                    cap_phash = _phash_ahash(cap_path)
                    dist = _hamming_distance_hex(nav_phash, cap_phash)
                    debug_info.setdefault("attemptPHash", []).append(
                        {"idx": idx, "phash": cap_phash, "dist": dist, "path": cap_path, "threshold": phash_threshold}
                    )

                    # Double-confirm to avoid false positives.
                    if nav_phash and cap_phash and dist >= phash_threshold:
                        await asyncio.sleep(0.25)
                        confirm_capture = await self.ui.screenshot_window(
                            owner_names=KUGOU_APP_NAMES,
                            tag=f"kugou_search_attempt_{idx}_confirm",
                        )
                        confirm_path = str(confirm_capture.get("screenshotPath") or "")
                        confirm_phash = _phash_ahash(confirm_path) if confirm_path else ""
                        dist2 = _hamming_distance_hex(nav_phash, confirm_phash)
                        debug_info.setdefault("attemptPHashConfirm", []).append(
                            {
                                "idx": idx,
                                "phash": confirm_phash,
                                "dist": dist2,
                                "path": confirm_path,
                                "threshold": phash_threshold,
                            }
                        )
                        if confirm_phash and dist2 >= phash_threshold:
                            found_results = True
                            break

                if not found_results:
                    last_path = str((last_attempt_capture or {}).get("screenshotPath") or "")
                    raise RuntimeError(f"未能进入酷狗搜索结果页（已尝试 {len(focus_points)} 个点位）。最后截图：{last_path}")

                # Best-effort: prefer "song" results (may fail due to OCR).
                await self._best_effort_click_any(
                    ["单曲", "歌曲", "综合"],
                    tag="kugou_tab_song",
                    dry_run=dry_run,
                    debug=debug_info,
                    click_text_kwargs={"roi": KUGOU_ROIS["full"], **KUGOU_OCR_CLICK_KWARGS},
                    allow_fail=True,
                )
                await asyncio.sleep(0.35)

                # 5) Play first search result.
                # Prefer OCR + geometry: anchor on the tab row (综合/单曲/歌曲), then click the first row below.
                try:
                    results_capture = await self.ui.screenshot_window(
                        owner_names=KUGOU_APP_NAMES,
                        tag="kugou_results_ocr_fallback",
                    )
                    debug_info.setdefault("captures", []).append({"step": "results_ocr_fallback", "capture": results_capture})

                    results_path = str((results_capture or {}).get("screenshotPath") or "")
                    iw = float(((results_capture.get("imageSize") or {}).get("width")) or 0.0)
                    ih = float(((results_capture.get("imageSize") or {}).get("height")) or 0.0)
                    boxes = await self.ui.ocr_screenshot_advanced(
                        results_path,
                        roi=KUGOU_ROIS["full"],
                        scale=1.0,
                        grayscale=True,
                        accurate=False,
                        custom_words=KUGOU_OCR_CLICK_KWARGS.get("ocr_custom_words"),
                    )

                    # 1) Prefer clicking the target song title text.
                    song_key = _target_song_from_query(query)
                    song_norm = _norm(song_key)

                    roi_list = _roi_px(rois["result_list_top"], iw=iw, ih=ih)

                    best_song_box = None
                    if song_norm:
                        best_y = None
                        for b in boxes:
                            if not _box_center_in_roi(b, roi_px=roi_list):
                                continue
                            try:
                                conf = float(getattr(b, "confidence", 0.0) or 0.0)
                            except Exception:
                                conf = 0.0
                            if conf < float(ocr_min_confidence):
                                continue

                            text_norm = _norm(str(getattr(b, "text", "") or ""))
                            if not text_norm:
                                continue

                            if song_norm not in text_norm:
                                continue

                            try:
                                y = float(getattr(b, "y", 0.0) or 0.0)
                            except Exception:
                                y = 0.0

                            if float(ih) > 1 and y < float(ih) * 0.15:
                                continue

                            if best_y is None or y < best_y:
                                best_y = y
                                best_song_box = b

                    if best_song_box is not None:
                        try:
                            cx, cy = best_song_box.center()
                        except Exception:
                            cx = None
                            cy = None

                        if cx is None or cy is None or float(iw) <= 1 or float(ih) <= 1:
                            raise RuntimeError("无法从 song box 计算点击点")

                        play_pt = _point(float(cx) / float(iw), float(cy) / float(ih))
                        debug_info.setdefault("clicks", []).append(
                            {
                                "step": "play_first",
                                "via": "song_text_fallback",
                                "songKey": song_key,
                                "matchedText": str(getattr(best_song_box, "text", "") or ""),
                                "point": play_pt,
                            }
                        )
                    else:
                        # 2) Fallback: anchor on the tab row (综合/单曲/歌曲), then click the first row below.
                        tab_box = None
                        for key in ["综合", "单曲", "歌曲"]:
                            for b in boxes:
                                try:
                                    conf = float(getattr(b, "confidence", 0.0) or 0.0)
                                except Exception:
                                    conf = 0.0
                                if conf < float(ocr_min_confidence):
                                    continue

                                text_norm = _norm(str(getattr(b, "text", "") or ""))
                                if not text_norm:
                                    continue

                                if _norm(key) in text_norm:
                                    tab_box = b
                                    break
                            if tab_box is not None:
                                break

                        if tab_box is None:
                            raise RuntimeError("未识别到结果页 tab 锚点（综合/单曲/歌曲）")

                        try:
                            y0 = float(getattr(tab_box, "y", 0.0) or 0.0)
                            h0 = float(getattr(tab_box, "height", 0.0) or 0.0)
                        except Exception:
                            y0 = 0.0
                            h0 = 0.0

                        offset_px = max(h0 * 2.0, float(ih) * 0.10)
                        y_click = (y0 + offset_px) / float(ih) if float(ih) > 1 else 0.30
                        y_click = max(0.20, min(0.78, float(y_click)))

                        play_pt = _point(0.28, y_click)
                        debug_info.setdefault("clicks", []).append(
                            {"step": "play_first", "via": "ocr_anchor_fallback", "anchor": str(getattr(tab_box, "text", "") or ""), "point": play_pt}
                        )
                except Exception as e:
                    debug_info.setdefault("warnings", []).append(f"定位第一行失败（fallback），回退坐标：{e}")
                    play_pt = _point(0.28, 0.28)
                    debug_info.setdefault("clicks", []).append({"step": "play_first", "via": "coord_fallback", "point": play_pt})

                await self.ui.click_at(play_pt["x"], play_pt["y"], clicks=1)
                await asyncio.sleep(0.12)
                await self.ui.key_code(36)
                await asyncio.sleep(0.9)

                if dry_run:
                    return {"message": f"(dry-run) 将在酷狗搜索并尝试播放第一首：{query}", "debug": debug_info if debug else None}

                after_capture = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_search_after_play")
                debug_info["afterCapture"] = after_capture

                # Stop early if playback is already confirmed.
                try:
                    pb = await _check_playback(tag_prefix="kugou_play_check_post")
                    if bool((pb or {}).get("confirmed")) is True:
                        return {
                            "message": f"已在酷狗播放：{(pb or {}).get('targetSong')}",
                            "debug": debug_info if debug else None,
                        }
                except Exception as e:
                    debug_info.setdefault("warnings", []).append(f"播放确认失败（忽略）：{e}")

                before_path = str((before_capture or {}).get("screenshotPath") or "")
                after_path = str(after_capture.get("screenshotPath") or "")
                if before_path and after_path:
                    before_hash = self.ui.file_sha256(before_path)
                    after_hash = self.ui.file_sha256(after_path)
                    debug_info["uiChange"] = {"before": before_hash, "after": after_hash, "same": before_hash == after_hash}
                    if before_hash and after_hash and before_hash == after_hash:
                        raise RuntimeError(
                            "已执行酷狗搜索/播放流程，但界面未发生变化，判定未进入搜索或未触发播放。"
                            f"已导出前后截图：{before_path} / {after_path}"
                        )

                return {"message": f"已在酷狗搜索并尝试播放第一首：{query}", "debug": debug_info if debug else None}

            async def _run_ocr_first() -> Dict[str, Any]:
                debug_info["mode"] = "ocr_first"

                try:
                    debug_info["frontmostProcess"] = await self.ui.get_frontmost_process_name()
                except Exception:
                    pass

                try:
                    await self.ui.set_process_frontmost("酷狗音乐")
                except Exception as e:
                    debug_info.setdefault("warnings", []).append(f"置前酷狗失败（忽略）：{e}")

                # Best-effort exit detail pages (avoid keys that may switch tabs/pages unexpectedly).
                key_attempts = [
                    {"name": "escape", "fn": lambda: self.ui.key_code(53)},
                    {"name": "cmd_left_bracket", "fn": lambda: self.ui.hotkey("[", modifiers=["command down"])},
                ]
                for item in key_attempts:
                    entry: dict[str, Any] = {"step": "go_home_key", "name": str(item.get("name") or ""), "ok": True}
                    try:
                        await item["fn"]()  # type: ignore[index]
                    except Exception as e:
                        entry["ok"] = False
                        entry["error"] = str(e)
                    debug_info.setdefault("goHome", []).append(entry)
                    await asyncio.sleep(0.30)

                base_capture = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_search_ocr_base")
                debug_info.setdefault("captures", []).append({"step": "ocr_base", "capture": base_capture})

                # NOTE: Use this capture to compute window bounds. pHash baseline should be taken after we
                # have reliably entered the search view; otherwise it will cause false positives.
                window_bounds = (base_capture or {}).get("windowBounds") or {}
                bx = float(window_bounds.get("x") or 0.0)
                by = float(window_bounds.get("y") or 0.0)
                bw = float(window_bounds.get("width") or 0.0)
                bh = float(window_bounds.get("height") or 0.0)
                if bw <= 1 or bh <= 1:
                    raise RuntimeError("无法获取酷狗窗口尺寸，无法执行搜索")

                def _point(x_ratio: float, y_ratio_from_top: float) -> dict[str, float]:
                    xr = max(0.0, min(1.0, float(x_ratio)))
                    yr = max(0.0, min(1.0, float(y_ratio_from_top)))
                    sx = bx + bw * xr
                    sy = by + bh * yr
                    return {"x": sx, "y": sy}

                def _norm(value: str) -> str:
                    v = str(value or "")
                    v = "".join(v.split())
                    v = v.replace("\uffff", "").replace("\ufffd", "")
                    return v.strip().lower()

                def _roi_px(roi: tuple[float, float, float, float], *, iw: float, ih: float) -> tuple[float, float, float, float]:
                    rx, ry, rw, rh = roi
                    return (float(rx) * iw, float(ry) * ih, float(rw) * iw, float(rh) * ih)

                def _box_center_in_roi(box: Any, *, roi_px: tuple[float, float, float, float]) -> bool:
                    x0, y0, w0, h0 = roi_px
                    x1 = x0 + w0
                    y1 = y0 + h0
                    try:
                        cx, cy = box.center()
                    except Exception:
                        return False
                    return (x0 <= float(cx) <= x1) and (y0 <= float(cy) <= y1)

                def _find_best_box(
                    boxes: List[Any],
                    *,
                    target: str,
                    match_mode: str,
                    min_conf: float,
                    roi_px: Optional[tuple[float, float, float, float]],
                ) -> Optional[Any]:
                    t_norm = _norm(target)
                    if not t_norm:
                        return None

                    candidates = []
                    for b in boxes:
                        try:
                            conf = float(getattr(b, "confidence", 0.0) or 0.0)
                        except Exception:
                            conf = 0.0
                        if conf < float(min_conf):
                            continue

                        text_norm = _norm(str(getattr(b, "text", "") or ""))
                        if not text_norm:
                            continue

                        ok = (text_norm == t_norm) if match_mode == "exact" else (t_norm in text_norm)
                        if not ok:
                            continue

                        if roi_px is not None and not _box_center_in_roi(b, roi_px=roi_px):
                            continue

                        candidates.append(b)

                    if not candidates:
                        return None

                    return max(candidates, key=lambda b: (float(getattr(b, "confidence", 0.0) or 0.0), float(getattr(b, "width", 0.0) or 0.0) * float(getattr(b, "height", 0.0) or 0.0)))

                async def _ocr_window(tag: str) -> tuple[Dict[str, Any], List[Any], float, float]:
                    cap = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=tag)
                    path = str(cap.get("screenshotPath") or "")
                    boxes = await self.ui.ocr_screenshot_advanced(
                        path,
                        roi=KUGOU_ROIS["full"],
                        scale=1.0,
                        grayscale=True,
                        accurate=False,
                        custom_words=KUGOU_OCR_CLICK_KWARGS.get("ocr_custom_words"),
                    )
                    iw = float(((cap.get("imageSize") or {}).get("width")) or 0.0)
                    ih = float(((cap.get("imageSize") or {}).get("height")) or 0.0)
                    return (cap, boxes, iw, ih)

                async def _detect_results_page_from_path(path: str, *, iw: float, ih: float) -> Dict[str, Any]:
                    """Detect whether the UI is already in search results page.

                    User-confirmed rules:
                    - Must see "取消" on the top search bar
                    - Must see >=50% of result tabs (综合/单曲/视频/歌单/听书/专辑/歌词)
                    """

                    boxes = await self.ui.ocr_screenshot_advanced(
                        path,
                        roi=KUGOU_ROIS["full"],
                        scale=1.0,
                        grayscale=True,
                        accurate=False,
                        custom_words=KUGOU_OCR_CLICK_KWARGS.get("ocr_custom_words"),
                    )

                    roi_top = _roi_px(rois["top_search"], iw=iw, ih=ih)
                    roi_tabs = _roi_px(rois["tabs"], iw=iw, ih=ih)

                    cancel_box = _find_best_box(
                        boxes,
                        target="取消",
                        match_mode="contains",
                        min_conf=ocr_min_confidence,
                        roi_px=roi_top,
                    )

                    tabs_candidates = ["综合", "单曲", "视频", "歌单", "听书", "专辑", "歌词"]
                    tab_hits = 0
                    for t in tabs_candidates:
                        if _find_best_box(
                            boxes,
                            target=t,
                            match_mode="contains",
                            min_conf=ocr_min_confidence,
                            roi_px=roi_tabs,
                        ):
                            tab_hits += 1

                    ratio = float(tab_hits) / float(len(tabs_candidates) or 1)
                    ok = bool((cancel_box is not None) and ratio >= 0.5)
                    return {
                        "ok": ok,
                        "cancel": bool(cancel_box is not None),
                        "tabHits": int(tab_hits),
                        "tabCount": int(len(tabs_candidates)),
                        "tabRatio": ratio,
                    }

                def _target_song_from_query(q: str) -> str:
                    parts = [p for p in str(q or "").split() if p]
                    if len(parts) >= 2:
                        return " ".join(parts[1:]).strip()
                    return str(q or "").strip()

                def _parse_mmss(text: str) -> Optional[int]:
                    m = re.search(r"(\d{1,2}):(\d{2})", str(text or ""))
                    if not m:
                        return None
                    try:
                        mm = int(m.group(1))
                        ss = int(m.group(2))
                        return mm * 60 + ss
                    except Exception:
                        return None

                async def _check_playback(*, tag_prefix: str) -> Dict[str, Any]:
                    target_song = _target_song_from_query(query)
                    target_norm = _norm(target_song)

                    captures: list[Dict[str, Any]] = []
                    texts: list[str] = []
                    seconds: list[Optional[int]] = []
                    bottom_hashes: list[str] = []

                    for idx in range(2):
                        cap = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=f"{tag_prefix}_{idx}")
                        captures.append(cap)
                        path = str(cap.get("screenshotPath") or "")
                        if not path:
                            texts.append("")
                            seconds.append(None)
                            bottom_hashes.append("")
                            continue

                        boxes = await self.ui.ocr_screenshot_advanced(
                            path,
                            roi=KUGOU_ROIS["bottom_player"],
                            scale=1.0,
                            grayscale=True,
                            accurate=False,
                            custom_words=KUGOU_OCR_CLICK_KWARGS.get("ocr_custom_words"),
                        )

                        # Weak fallback: bottom-area hash changes with progress.
                        bmp = _sips_bmp_bytes(path, max_size=96)
                        bottom_hashes.append(_bmp_ahash(bmp, crop_left=0.0, crop_right=1.0, crop_top=0.86, crop_bottom=0.99))

                        items = []
                        for b in boxes:
                            txt = str(getattr(b, "text", "") or "").strip()
                            if txt:
                                items.append(txt)
                        joined = " ".join(items)
                        texts.append(joined)
                        seconds.append(_parse_mmss(joined))

                        if idx == 0:
                            await asyncio.sleep(1.0)

                    text0 = str(texts[0] if len(texts) > 0 else "")
                    text1 = str(texts[1] if len(texts) > 1 else "")
                    n0 = _norm(text0)
                    n1 = _norm(text1)

                    sim0 = difflib.SequenceMatcher(None, target_norm, n0).ratio() if target_norm and n0 else 0.0
                    sim1 = difflib.SequenceMatcher(None, target_norm, n1).ratio() if target_norm and n1 else 0.0
                    match_ok = bool((target_norm and target_norm in n0) or (target_norm and target_norm in n1) or (max(sim0, sim1) >= 0.6))

                    t0 = seconds[0] if len(seconds) > 0 else None
                    t1 = seconds[1] if len(seconds) > 1 else None
                    progress_ok = bool((t0 is not None) and (t1 is not None) and (int(t1) > int(t0)))

                    h0 = bottom_hashes[0] if len(bottom_hashes) > 0 else ""
                    h1 = bottom_hashes[1] if len(bottom_hashes) > 1 else ""
                    hash_delta = _hamming_distance_hex(h0, h1) if h0 and h1 else 10**9
                    hash_progress_ok = bool(hash_delta != 10**9 and hash_delta >= 6)

                    confirmed = bool(match_ok and (progress_ok or hash_progress_ok))
                    payload = {
                        "targetSong": target_song,
                        "targetNorm": target_norm,
                        "confirmed": confirmed,
                        "matchOk": match_ok,
                        "progressOk": progress_ok,
                        "hashProgressOk": hash_progress_ok,
                        "hashDelta": hash_delta,
                        "samples": [
                            {"text": text0, "seconds": t0, "capture": captures[0]},
                            {"text": text1, "seconds": t1, "capture": captures[1]},
                        ],
                    }
                    debug_info.setdefault("playbackCheck", []).append(payload)
                    return payload

                def _point_from_box(box: Any, *, iw: float, ih: float) -> Optional[dict[str, float]]:
                    try:
                        cx, cy = box.center()
                    except Exception:
                        return None

                    if iw <= 1 or ih <= 1:
                        return None

                    xr = float(cx) / float(iw)
                    yr = float(cy) / float(ih)
                    return _point(xr, yr)

                async def _detect_ui_state(*, tag: str) -> Dict[str, Any]:
                    cap, boxes, iw, ih = await _ocr_window(tag)
                    debug_info.setdefault("captures", []).append({"step": "ocr_state", "capture": cap})

                    roi_top = _roi_px(rois["top_search"], iw=iw, ih=ih)

                    cancel_box = _find_best_box(
                        boxes,
                        target="取消",
                        match_mode="contains",
                        min_conf=ocr_min_confidence,
                        roi_px=roi_top,
                    )
                    history_box = _find_best_box(
                        boxes,
                        target="历史搜索",
                        match_mode="contains",
                        min_conf=ocr_min_confidence,
                        roi_px=None,
                    )
                    search_box = _find_best_box(
                        boxes,
                        target="搜索",
                        match_mode="contains",
                        min_conf=ocr_min_confidence,
                        roi_px=roi_top,
                    )

                    nav_hits = 0
                    for key in ["推荐", "频道", "歌单", "歌手"]:
                        if _find_best_box(boxes, target=key, match_mode="contains", min_conf=ocr_min_confidence, roi_px=None):
                            nav_hits += 1

                    is_search_view = bool(cancel_box is not None or history_box is not None)

                    # Main music page must satisfy BOTH:
                    # - see >=3/4 top tabs (推荐/频道/歌单/歌手)
                    # - see "搜索" entry in the top search ROI (otherwise it may be blocked by a panel)
                    is_music_shell = bool(nav_hits >= 3)
                    is_home_search_ready = bool((not is_search_view) and is_music_shell and (search_box is not None))
                    is_music_shell_blocked = bool((not is_search_view) and is_music_shell and (search_box is None))

                    if is_search_view:
                        mode = "search_view"
                    elif is_home_search_ready:
                        mode = "home_search_ready"
                    elif is_music_shell_blocked:
                        mode = "music_shell_blocked"
                    else:
                        mode = "needs_music"

                    payload: Dict[str, Any] = {
                        "mode": mode,
                        "cancel": bool(cancel_box is not None),
                        "history": bool(history_box is not None),
                        "search": bool(search_box is not None),
                        "navHits": nav_hits,
                        "musicShell": is_music_shell,
                        "musicShellBlocked": is_music_shell_blocked,
                    }

                    search_pt = _point_from_box(search_box, iw=iw, ih=ih) if search_box is not None else None
                    if search_pt:
                        payload["searchPoint"] = search_pt

                    debug_info["searchState"] = payload
                    return payload

                async def _ensure_search_view() -> Dict[str, Any]:
                    """Ensure we are in search view.

                    Kugou UI has many sub-pages/overlays. We first converge to a state where the search bar is visible,
                    then enter the search view (which shows '取消'/'历史搜索').
                    """

                    # Stable back button click points.
                    # Avoid clicking the avatar area on the music shell.
                    back_global_points = [
                        (0.12, 0.085),
                        (0.14, 0.085),
                        (0.16, 0.085),
                        (0.14, 0.11),
                    ]
                    back_panel_points = [
                        (0.54, 0.085),
                        (0.52, 0.085),
                        (0.56, 0.085),
                        (0.54, 0.11),
                    ]

                    gray_area_points = [
                        (0.25, 0.22),
                        (0.25, 0.32),
                        (0.22, 0.28),
                    ]

                    key_back_attempts = [
                        {"name": "escape", "fn": lambda: self.ui.key_code(53)},
                        {"name": "cmd_left_bracket", "fn": lambda: self.ui.hotkey("[", modifiers=["command down"])},
                    ]

                    last_state: Dict[str, Any] = {}
                    for round_idx in range(10):
                        state = await _detect_ui_state(tag=f"kugou_ui_state_round_{round_idx}")
                        debug_info.setdefault("uiStateRounds", []).append({"round": round_idx, "state": state})
                        last_state = state

                        if state.get("mode") == "search_view":
                            return state

                        # If we're not in search view, always try to converge back to the music shell first.
                        # This fixes cases where an early mis-click enters "我的/个人主页".
                        if state.get("mode") == "needs_back":
                            try:
                                info = await self.ui.click_text(
                                    "音乐",
                                    tag=f"kugou_ocr_nav_music_round_{round_idx}",
                                    clicks=1,
                                    match_mode="contains",
                                    min_confidence=ocr_min_confidence,
                                    window_owner_names=KUGOU_APP_NAMES,
                                    roi=KUGOU_ROIS["full"],
                                    **KUGOU_OCR_CLICK_KWARGS,
                                    dry_run=dry_run,
                                )
                                debug_info.setdefault("clicks", []).append({"step": "ocr_nav_music", "round": round_idx, "result": info})
                            except Exception as e:
                                debug_info.setdefault("warnings", []).append(f"OCR 点击‘音乐’失败（round={round_idx}），改用坐标兜底：{e}")
                                nav_music_points = [
                                    (0.055, 0.42),
                                    (0.055, 0.40),
                                    (0.055, 0.44),
                                ]
                                for idx, (xr, yr) in enumerate(nav_music_points):
                                    pt = _point(xr, yr)
                                    await self.ui.click_at(pt["x"], pt["y"], clicks=1)
                                    debug_info.setdefault("clicks", []).append(
                                        {"step": "nav_music", "round": round_idx, "idx": idx, "point": pt}
                                    )
                                    await asyncio.sleep(0.10)

                            await asyncio.sleep(0.22)
                            state = await _detect_ui_state(tag=f"kugou_ui_state_round_{round_idx}_after_music")
                            debug_info.setdefault("uiStateRounds", []).append({"round": round_idx, "state": state})
                            last_state = state

                            if state.get("mode") == "search_view":
                                return state

                        if state.get("mode") == "home_search_ready":
                            # Click the search placeholder within top search ROI.
                            try:
                                pt = state.get("searchPoint")
                                if isinstance(pt, dict) and pt.get("x") and pt.get("y"):
                                    await self.ui.click_at(float(pt["x"]), float(pt["y"]), clicks=1)
                                    debug_info.setdefault("clicks", []).append({"step": "enter_search", "via": "searchPoint", "point": pt})
                                else:
                                    # Coordinate-first: more reliable than OCR on some themes.
                                    for idx, (xr, yr) in enumerate([
                                        (0.72, 0.085),
                                        (0.70, 0.085),
                                        (0.76, 0.085),
                                        (0.72, 0.11),
                                    ]):
                                        p = _point(xr, yr)
                                        await self.ui.click_at(p["x"], p["y"], clicks=1)
                                        debug_info.setdefault("clicks", []).append(
                                            {"step": "enter_search", "via": "coord", "idx": idx, "point": p}
                                        )
                                        await asyncio.sleep(0.10)

                                    info = await self.ui.click_text(
                                        "搜索",
                                        tag="kugou_ocr_enter_search",
                                        clicks=1,
                                        match_mode="contains",
                                        min_confidence=ocr_min_confidence,
                                        window_owner_names=KUGOU_APP_NAMES,
                                        roi=KUGOU_ROIS["top_search"],
                                        **KUGOU_OCR_CLICK_KWARGS,
                                        dry_run=dry_run,
                                    )
                                    debug_info.setdefault("clicks", []).append({"step": "enter_search", "via": "ocr", "result": info})
                            except Exception as e:
                                debug_info.setdefault("warnings", []).append(f"进入搜索页失败（将尝试返回后重试）：{e}")

                            await asyncio.sleep(0.25)
                            continue

                        # needs_back: prefer clicking back button repeatedly.
                        for kind, points in [("back_panel", back_panel_points), ("back_global", back_global_points)]:
                            for idx, (xr, yr) in enumerate(points):
                                pt = _point(xr, yr)
                                await self.ui.click_at(pt["x"], pt["y"], clicks=1)
                                debug_info.setdefault("clicks", []).append(
                                    {"step": "go_back", "kind": kind, "idx": idx, "point": pt, "round": round_idx}
                                )
                                await asyncio.sleep(0.22)

                                state2 = await _detect_ui_state(tag=f"kugou_ui_state_after_{kind}_{round_idx}_{idx}")
                                debug_info.setdefault("uiStateRounds", []).append({"round": round_idx, "state": state2})
                                last_state = state2

                                if state2.get("mode") != "needs_back":
                                    break
                            if last_state.get("mode") != "needs_back":
                                break

                        if last_state.get("mode") != "needs_back":
                            continue

                        # Fallback 1: click gray area on the left content grid.
                        for idx, (xr, yr) in enumerate(gray_area_points):
                            pt = _point(xr, yr)
                            await self.ui.click_at(pt["x"], pt["y"], clicks=1)
                            debug_info.setdefault("clicks", []).append(
                                {"step": "go_back", "kind": "gray_area", "idx": idx, "point": pt, "round": round_idx}
                            )
                            await asyncio.sleep(0.22)

                            state3 = await _detect_ui_state(tag=f"kugou_ui_state_after_gray_{round_idx}_{idx}")
                            debug_info.setdefault("uiStateRounds", []).append({"round": round_idx, "state": state3})
                            last_state = state3

                            if state3.get("mode") != "needs_back":
                                break

                        if last_state.get("mode") != "needs_back":
                            continue

                        # Fallback 2: keyboard back.
                        for item in key_back_attempts:
                            entry: dict[str, Any] = {
                                "step": "go_back_key",
                                "name": str(item.get("name") or ""),
                                "ok": True,
                                "round": round_idx,
                            }
                            try:
                                await item["fn"]()  # type: ignore[index]
                            except Exception as e:
                                entry["ok"] = False
                                entry["error"] = str(e)
                            debug_info.setdefault("goBackKey", []).append(entry)
                            await asyncio.sleep(0.25)

                            state4 = await _detect_ui_state(tag=f"kugou_ui_state_after_key_{round_idx}_{entry['name']}")
                            debug_info.setdefault("uiStateRounds", []).append({"round": round_idx, "state": state4})
                            last_state = state4

                            if state4.get("mode") != "needs_back":
                                break

                    return last_state

                # 1) Ensure we are in the "音乐" page (best-effort).
                try:
                    info = await self.ui.click_text(
                        "音乐",
                        tag="kugou_ocr_nav_music",
                        clicks=1,
                        match_mode="contains",
                        min_confidence=ocr_min_confidence,
                        window_owner_names=KUGOU_APP_NAMES,
                        roi=KUGOU_ROIS["full"],
                        **KUGOU_OCR_CLICK_KWARGS,
                        dry_run=dry_run,
                    )
                    debug_info.setdefault("clicks", []).append({"step": "ocr_nav_music", "result": info})
                    await asyncio.sleep(0.25)
                except Exception as e:
                    debug_info.setdefault("warnings", []).append(f"OCR 点击‘音乐’失败，改用坐标兜底：{e}")
                    nav_music_points = [
                        (0.055, 0.42),
                        (0.055, 0.40),
                        (0.055, 0.44),
                    ]
                    for idx, (xr, yr) in enumerate(nav_music_points):
                        pt = _point(xr, yr)
                        await self.ui.click_at(pt["x"], pt["y"], clicks=1)
                        debug_info.setdefault("clicks", []).append({"step": "nav_music", "idx": idx, "point": pt})
                        await asyncio.sleep(0.25)

                # 2) Converge to search view.
                state = await _ensure_search_view()
                if (state or {}).get("mode") != "search_view":
                    raise RuntimeError(f"未能返回到可搜索界面并进入搜索页：{state}")

                # Baseline pHash in search view (before typing query).
                before_input_capture = await self.ui.screenshot_window(
                    owner_names=KUGOU_APP_NAMES,
                    tag="kugou_search_ocr_before_input",
                )
                debug_info.setdefault("captures", []).append({"step": "ocr_before_input", "capture": before_input_capture})

                base_path = str((before_input_capture or {}).get("screenshotPath") or "")
                base_phash = _phash_ahash(base_path) if base_path else ""
                debug_info["basePHash"] = base_phash
                phash_threshold = 20

                if not dry_run and not base_phash:
                    raise RuntimeError("无法计算酷狗窗口搜索页基线 pHash，无法可靠判定是否进入结果页")

                focus_points = [
                    (0.52, 0.055),
                    (0.52, 0.070),
                    (0.50, 0.055),
                    (0.50, 0.085),
                    (0.35, 0.070),
                    (0.70, 0.070),
                    (0.85, 0.070),
                    (0.72, 0.070),
                ]

                # 3) Focus input then paste query.
                found_results = False
                last_attempt_path = ""
                for idx, (xr, yr) in enumerate(focus_points):
                    focus_pt = _point(xr, yr)
                    await self.ui.click_at(focus_pt["x"], focus_pt["y"], clicks=1)

                    focus_info = None
                    try:
                        focus_info = await self.ui.get_focused_ui_element_info("酷狗音乐")
                    except Exception:
                        focus_info = None

                    debug_info.setdefault("focus", []).append(
                        {"idx": idx, "point": focus_pt, "focused": focus_info}
                    )
                    await asyncio.sleep(0.18)

                    # Only proceed if focus looks like a text input.
                    if isinstance(focus_info, dict) and focus_info.get("ok") is True:
                        role = str(focus_info.get("role") or "")
                        if ("Text" not in role) and ("Field" not in role):
                            continue

                    try:
                        await self.ui.hotkey("a", modifiers=["command down"])
                        await self.ui.key_code(51)
                    except Exception as e:
                        debug_info.setdefault("warnings", []).append(f"清空输入框失败（忽略）：{e}")

                    _pbcopy(query)
                    try:
                        await self.ui.hotkey("v", modifiers=["command down"])
                    except Exception as e:
                        debug_info.setdefault("warnings", []).append(f"粘贴搜索词失败（忽略）：{e}")

                    await asyncio.sleep(0.35)

                    # Prefer selecting the first dropdown suggestion, then enter to search.
                    await self.ui.key_code(125)  # Down
                    await asyncio.sleep(0.12)
                    await self.ui.key_code(36)  # Enter
                    await asyncio.sleep(0.85)

                    if dry_run:
                        found_results = True
                        break

                    attempt_capture = await self.ui.screenshot_window(
                        owner_names=KUGOU_APP_NAMES,
                        tag=f"kugou_search_ocr_attempt_{idx}",
                    )
                    debug_info.setdefault("captures", []).append(
                        {"step": "ocr_search_attempt", "idx": idx, "capture": attempt_capture}
                    )

                    cap_path = str(attempt_capture.get("screenshotPath") or "")
                    last_attempt_path = cap_path

                    # Prefer OCR-based results-page detection: must see "取消" + >=50% of tabs.
                    try:
                        iw = float(((attempt_capture.get("imageSize") or {}).get("width")) or 0.0)
                        ih = float(((attempt_capture.get("imageSize") or {}).get("height")) or 0.0)
                        if cap_path and float(iw) > 1 and float(ih) > 1:
                            results_info = await _detect_results_page_from_path(cap_path, iw=iw, ih=ih)
                            debug_info.setdefault("resultsPageDetect", []).append({"idx": idx, **(results_info or {})})
                            if bool((results_info or {}).get("ok")) is True:
                                found_results = True
                                break
                    except Exception as e:
                        debug_info.setdefault("warnings", []).append(f"结果页 OCR 判定失败（忽略，回退 pHash）：{e}")

                    cap_phash = _phash_ahash(cap_path) if cap_path else ""
                    dist = _hamming_distance_hex(base_phash, cap_phash)
                    debug_info.setdefault("attemptPHash", []).append(
                        {"idx": idx, "phash": cap_phash, "dist": dist, "path": cap_path, "threshold": phash_threshold}
                    )

                    # Fallback to pHash if OCR is flaky.
                    if base_phash and cap_phash and dist >= phash_threshold:
                        found_results = True
                        break

                if not found_results:
                    raise RuntimeError(f"OCR-first 未能可靠进入搜索结果页。最后截图：{last_attempt_path}")

                # Best-effort: prefer "song" results.
                await self._best_effort_click_any(
                    ["单曲", "歌曲", "综合"],
                    tag="kugou_tab_song",
                    dry_run=dry_run,
                    debug=debug_info,
                    min_confidence=ocr_min_confidence,
                    click_text_kwargs={"roi": KUGOU_ROIS["full"], **KUGOU_OCR_CLICK_KWARGS},
                    allow_fail=True,
                )
                await asyncio.sleep(0.30)

                # Play first search result.
                # Use OCR + geometry to locate the first row below the tab bar.
                try:
                    cap, boxes, iw, ih = await _ocr_window("kugou_results_ocr")
                    debug_info.setdefault("captures", []).append({"step": "results_ocr", "capture": cap})

                    # 1) Prefer clicking the target song title text (most reliable).
                    song_key = _target_song_from_query(query)
                    song_norm = _norm(song_key)

                    roi_list = _roi_px(rois["result_list_top"], iw=iw, ih=ih)

                    best_song_box = None
                    if song_norm:
                        best_y = None
                        for b in boxes:
                            if not _box_center_in_roi(b, roi_px=roi_list):
                                continue
                            try:
                                conf = float(getattr(b, "confidence", 0.0) or 0.0)
                            except Exception:
                                conf = 0.0
                            if conf < float(ocr_min_confidence):
                                continue

                            text_norm = _norm(str(getattr(b, "text", "") or ""))
                            if not text_norm:
                                continue

                            if song_norm not in text_norm:
                                continue

                            try:
                                y = float(getattr(b, "y", 0.0) or 0.0)
                            except Exception:
                                y = 0.0

                            if float(ih) > 1 and y < float(ih) * 0.15:
                                continue

                            if best_y is None or y < best_y:
                                best_y = y
                                best_song_box = b

                    if best_song_box is not None:
                        p = _point_from_box(best_song_box, iw=iw, ih=ih)
                        if not p:
                            raise RuntimeError("无法从 song box 计算点击点")
                        play_pt = p
                        debug_info.setdefault("clicks", []).append(
                            {
                                "step": "play_first",
                                "via": "song_text",
                                "songKey": song_key,
                                "matchedText": str(getattr(best_song_box, "text", "") or ""),
                                "point": play_pt,
                            }
                        )
                    else:
                        # 2) Fallback: anchor on the tab row (综合/单曲/歌曲), then click the first row below.
                        tab_box = None
                        for key in ["综合", "单曲", "歌曲"]:
                            tab_box = _find_best_box(
                                boxes,
                                target=key,
                                match_mode="contains",
                                min_conf=ocr_min_confidence,
                                roi_px=None,
                            )
                            if tab_box is not None:
                                break

                        if tab_box is None:
                            raise RuntimeError("未识别到结果页 tab 锚点（综合/单曲/歌曲）")

                        try:
                            y0 = float(getattr(tab_box, "y", 0.0) or 0.0)
                            h0 = float(getattr(tab_box, "height", 0.0) or 0.0)
                        except Exception:
                            y0 = 0.0
                            h0 = 0.0

                        offset_px = max(h0 * 2.0, float(ih) * 0.10)
                        y_click = (y0 + offset_px) / float(ih) if float(ih) > 1 else 0.30
                        y_click = max(0.20, min(0.78, float(y_click)))
                        play_pt = _point(0.28, y_click)
                        debug_info.setdefault("clicks", []).append(
                            {
                                "step": "play_first",
                                "via": "ocr_anchor",
                                "anchor": str(getattr(tab_box, "text", "") or ""),
                                "point": play_pt,
                            }
                        )
                except Exception as e:
                    # Fallback: click near the top of result list to reduce selecting 5th+ rows.
                    debug_info.setdefault("warnings", []).append(f"定位第一行失败，回退到坐标点击：{e}")
                    play_pt = _point(0.28, 0.28)
                    debug_info.setdefault("clicks", []).append({"step": "play_first", "via": "coord_fallback", "point": play_pt})

                await self.ui.click_at(play_pt["x"], play_pt["y"], clicks=1)
                await asyncio.sleep(0.12)
                await self.ui.key_code(36)
                await asyncio.sleep(0.9)

                if dry_run:
                    return {"message": f"(dry-run) 将在酷狗搜索并尝试播放第一首：{query}", "debug": debug_info if debug else None}

                after_capture = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_search_after_play_ocr")
                debug_info["afterCapture"] = after_capture

                # Stop early if playback is already confirmed (avoid repeating search attempts).
                try:
                    pb = await _check_playback(tag_prefix="kugou_play_check_post_ocr")
                    if bool((pb or {}).get("confirmed")) is True:
                        return {
                            "message": f"已在酷狗播放：{(pb or {}).get('targetSong')}",
                            "debug": debug_info if debug else None,
                        }
                except Exception as e:
                    debug_info.setdefault("warnings", []).append(f"播放确认失败（忽略）：{e}")

                before_path = str((before_input_capture or {}).get("screenshotPath") or "")
                after_path = str(after_capture.get("screenshotPath") or "")
                if before_path and after_path:
                    before_hash = self.ui.file_sha256(before_path)
                    after_hash = self.ui.file_sha256(after_path)

                    after_phash = _phash_ahash(after_path)
                    dist = _hamming_distance_hex(base_phash, after_phash)

                    debug_info["uiChange"] = {
                        "beforeSha256": before_hash,
                        "afterSha256": after_hash,
                        "beforePHash": base_phash,
                        "afterPHash": after_phash,
                        "dist": dist,
                        "threshold": phash_threshold,
                    }

                    # Prefer pHash-based validation to avoid false positives caused by minor UI changes.
                    if base_phash and after_phash and dist < phash_threshold:
                        raise RuntimeError(
                            "已执行 OCR 优先酷狗搜索/播放流程，但未能可靠判定进入搜索结果页。"
                            f"已导出前后截图：{before_path} / {after_path}"
                        )

                    if before_hash and after_hash and before_hash == after_hash:
                        raise RuntimeError(
                            "已执行 OCR 优先酷狗搜索/播放流程，但界面未发生变化，判定未进入搜索或未触发播放。"
                            f"已导出前后截图：{before_path} / {after_path}"
                        )

                return {"message": f"已在酷狗搜索并尝试播放第一首：{query}", "debug": debug_info if debug else None}

            try:
                return await _run_ocr_first()
            except Exception as e:
                debug_info.setdefault("warnings", []).append(f"OCR 优先链路失败，回退坐标兜底：{e}")
                return await _run_coordinate_fallback()

        raise RuntimeError("酷狗当前不支持该 action")

    async def _open_and_activate_kugou(self) -> None:
        """Open and force KuGou to be frontmost (best-effort).

        IMPORTANT: UI clicks are always delivered to the current frontmost app on macOS.
        If KuGou is not frontmost (e.g. in another Space), any click may hit a different window.
        """

        # Try common app names.
        for name in KUGOU_APP_NAMES:
            try:
                subprocess.run(["open", "-a", name], capture_output=True, text=True)
            except Exception:
                pass

            for _ in range(3):
                try:
                    await self.ui.activate_app(name)
                except Exception:
                    continue

                try:
                    await self.ui.set_process_frontmost(name)
                except Exception:
                    pass

                await asyncio.sleep(0.15)
                try:
                    frontmost = await self.ui.get_frontmost_process_name()
                except Exception:
                    frontmost = ""

                if str(frontmost).strip() in set(KUGOU_APP_NAMES):
                    return

        raise RuntimeError("无法打开或置前酷狗音乐，请确认已安装且已授予辅助功能权限")

    async def _kugou_search_ocr_workflow(self, *, query: str, debug: bool, dry_run: bool) -> Dict[str, Any]:
        """KuGou search-and-play workflow using OCR-only navigation.

        Constraints (user confirmed):
        - Before clicking sidebar "音乐": normalize KuGou window to 1152x801 and center it on main screen.
        - Always click sidebar "音乐" first (precise OCR click) to enter music page.
        - Main music page must satisfy: tabs hits >=3/4 AND can see "搜索" in top search ROI.
        - If tabs are visible but "搜索" is missing, treat as an overlay panel (small/right or large) and
          close it by clicking the panel's own top-left back button (OCR-detected).
        - Enter search, submit query, then in results page: click "单曲" -> click the target song title (single click).
        - After clicking the song title, verify playback via bottom bar OCR and progress/hash changes.
        - No coordinate/keyboard fallbacks for navigation (typing/search submit still uses keyboard).
        """

        debug_info: Dict[str, Any] = {"mode": "kugou_ocr_workflow_v2"}

        # Split thresholds: state detection should be tolerant; actual click targets should be stricter.
        detect_min_confidence = 0.35
        click_min_confidence = 0.60

        warp_enabled = str(os.environ.get("VOICE_ASSISTANT_DEBUG_WARP") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "on",
        }
        debug_info["debugWarpEnabled"] = bool(warp_enabled)

        async def _ensure_kugou_frontmost(*, step: str) -> None:
            before = None
            after = None
            err = None

            try:
                before = await self.ui.get_frontmost_process_name()
            except Exception as e:
                err = str(e)

            try:
                # Use an explicit process name; OCR/click flows assume KuGou is the frontmost app.
                await self.ui.activate_app("酷狗音乐")
            except Exception:
                pass

            try:
                await self.ui.set_process_frontmost("酷狗音乐")
            except Exception:
                pass

            await asyncio.sleep(0.12)
            try:
                after = await self.ui.get_frontmost_process_name()
            except Exception as e:
                err = str(e)

            evidence = {
                "step": str(step),
                "before": before,
                "after": after,
                "ok": str(after).strip() in set(KUGOU_APP_NAMES),
                "error": err,
            }
            debug_info.setdefault("frontmostChecks", []).append(evidence)

            if evidence["ok"] is not True:
                raise RuntimeError(f"前台应用不是酷狗，已中止点击以避免误操作：{evidence}")

        async def _click_screen_point(
            cap: Dict[str, Any],
            *,
            sx: float,
            sy: float,
            step: str,
            clicks: int = 1,
            ocr_box: Optional[dict[str, Any]] = None,
        ) -> dict[str, Any]:
            # Guardrail: ensure KuGou is the frontmost app, otherwise clicks will hit the wrong window.
            await _ensure_kugou_frontmost(step=f"{step}_preclick")

            if bool(warp_enabled):
                return await self.ui.click_at_debug(
                    float(sx),
                    float(sy),
                    clicks=int(clicks),
                    tag=str(step),
                    warp_cursor=True,
                    settle_sec=0.03,
                    window_bounds=cap.get("windowBounds") or {},
                    image_size=cap.get("imageSize") or {},
                    ocr_box=ocr_box,
                )

            await self.ui.click_at(float(sx), float(sy), clicks=int(clicks))
            click_debug: dict[str, Any] = {"targetPoint": {"x": float(sx), "y": float(sy)}, "warpCursor": False}

            if ocr_box is not None:
                try:
                    ix, iy = self.ui._to_window_image_point_from_screen_point(  # noqa: SLF001
                        float(sx),
                        float(sy),
                        window_bounds=cap.get("windowBounds") or {},
                        image_size=cap.get("imageSize") or {},
                    )
                    bx = float(ocr_box.get("x") or 0.0)
                    by = float(ocr_box.get("y") or 0.0)
                    bw = float(ocr_box.get("width") or 0.0)
                    bh = float(ocr_box.get("height") or 0.0)
                    in_box = bool((bx <= float(ix) <= bx + bw) and (by <= float(iy) <= by + bh))
                    click_debug["targetAsImagePoint"] = {"x": float(ix), "y": float(iy)}
                    click_debug["ocrHitTest"] = {
                        "ok": True,
                        "inBox": in_box,
                        "box": {
                            "text": str(ocr_box.get("text") or ""),
                            "x": bx,
                            "y": by,
                            "width": bw,
                            "height": bh,
                        },
                        "deltaToBoxCenter": {
                            "dx": float(ix) - (bx + bw / 2.0),
                            "dy": float(iy) - (by + bh / 2.0),
                        },
                    }
                except Exception as e:
                    click_debug["ocrHitTest"] = {"ok": False, "error": str(e)}

            return click_debug

        if dry_run:
            return {"message": f"(dry-run) 将按 OCR 工作流在酷狗搜索并播放：{query}", "debug": debug_info if debug else None}

        await self._open_and_activate_kugou()
        try:
            await self.ui.set_process_frontmost("酷狗音乐")
        except Exception as e:
            debug_info.setdefault("warnings", []).append(f"置前酷狗失败（忽略）：{e}")

        def _workflow_debug_dir() -> Path:
            base = Path.home() / "Documents" / "VoiceAssistant" / "ui_debug"
            run_id = str(os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or "").strip()
            out = base / run_id if run_id else base
            out.mkdir(parents=True, exist_ok=True)
            return out

        def _dump_debug_info(stage: str, *, error: Optional[str] = None) -> Optional[str]:
            """Best-effort dump of current debug_info for post-mortem analysis."""

            try:
                ts = int(time.time() * 1000)
                out_dir = _workflow_debug_dir()
                out_path = out_dir / f"kugou_ocr_workflow_debug_{stage}_{ts}.json"
                payload = {
                    "timestampMs": ts,
                    "stage": str(stage or ""),
                    "error": str(error or "") if error else "",
                    "debug": debug_info,
                }
                out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                debug_info.setdefault("debugDumps", []).append(str(out_path))
                return str(out_path)
            except Exception as e:
                debug_info.setdefault("warnings", []).append(f"debug_info 落盘失败（忽略）：{e}")
                return None

        # Capture initial state BEFORE any click for traceability.
        try:
            init_cap = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_flow_init")
            debug_info.setdefault("captures", []).append({"step": "kugou_flow_init", "capture": init_cap})
        except Exception as e:
            debug_info.setdefault("warnings", []).append(f"初始截图失败（忽略）：{e}")
        _dump_debug_info("init")

        # Normalize window size/position early to reduce ROI/OCR brittleness.
        try:
            await _ensure_kugou_frontmost(step="kugou_window_normalize")
            norm = await self.ui.normalize_process_window(
                process_name="酷狗音乐",
                width=1152,
                height=801,
                center_main_screen=True,
            )
            debug_info["windowNormalize"] = norm
            await asyncio.sleep(0.25)
            cap_norm = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_window_normalized")
            debug_info.setdefault("captures", []).append({"step": "kugou_window_normalized", "capture": cap_norm})
        except Exception as e:
            _dump_debug_info("error_window_normalize_failed", error=str(e))
            raise RuntimeError(f"酷狗窗口归一化失败：{e}")

        def _norm(value: str) -> str:
            v = str(value or "")
            v = "".join(v.split())
            v = v.replace("\uffff", "").replace("\ufffd", "")
            return v.strip().lower()

        def _target_song_from_query(q: str) -> str:
            parts = [p for p in str(q or "").split() if p]
            if len(parts) >= 2:
                return " ".join(parts[1:]).strip()
            return str(q or "").strip()

        def _parse_mmss(text: str) -> Optional[int]:
            m = re.search(r"(\d{1,2}):(\d{2})", str(text or ""))
            if not m:
                return None
            try:
                mm = int(m.group(1))
                ss = int(m.group(2))
                return mm * 60 + ss
            except Exception:
                return None

        async def _check_playback(*, tag_prefix: str) -> Dict[str, Any]:
            target_song = _target_song_from_query(query)
            target_norm = _norm(target_song)

            captures = []
            texts = []
            seconds = []
            bottom_hashes = []
            for idx in range(2):
                cap = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=f"{tag_prefix}_{idx}")
                captures.append(cap)
                path = str(cap.get("screenshotPath") or "")
                if not path:
                    texts.append("")
                    seconds.append(None)
                    bottom_hashes.append("")
                    continue

                boxes0 = await self.ui.ocr_screenshot_advanced(
                    path,
                    roi=KUGOU_ROIS["bottom_player"],
                    scale=1.0,
                    grayscale=True,
                    accurate=False,
                    custom_words=KUGOU_OCR_CLICK_KWARGS.get("ocr_custom_words") or [],
                )

                bmp = _sips_bmp_bytes(path, max_size=96)
                bottom_hashes.append(
                    _bmp_ahash(
                        bmp,
                        crop_left=0.0,
                        crop_right=1.0,
                        crop_top=0.86,
                        crop_bottom=0.99,
                    )
                )

                items = []
                for b in boxes0:
                    txt = str(getattr(b, "text", "") or "").strip()
                    if txt:
                        items.append(txt)
                joined = " ".join(items)
                texts.append(joined)
                seconds.append(_parse_mmss(joined))

                if idx == 0:
                    await asyncio.sleep(1.0)

            text0 = str(texts[0] if len(texts) > 0 else "")
            text1 = str(texts[1] if len(texts) > 1 else "")
            n0 = _norm(text0)
            n1 = _norm(text1)

            sim0 = difflib.SequenceMatcher(None, target_norm, n0).ratio() if target_norm and n0 else 0.0
            sim1 = difflib.SequenceMatcher(None, target_norm, n1).ratio() if target_norm and n1 else 0.0
            match_ok = bool(
                (target_norm and target_norm in n0)
                or (target_norm and target_norm in n1)
                or (max(sim0, sim1) >= 0.6)
            )

            t0 = seconds[0] if len(seconds) > 0 else None
            t1 = seconds[1] if len(seconds) > 1 else None
            progress_ok = bool((t0 is not None) and (t1 is not None) and (int(t1) > int(t0)))

            h0 = bottom_hashes[0] if len(bottom_hashes) > 0 else ""
            h1 = bottom_hashes[1] if len(bottom_hashes) > 1 else ""
            hash_delta = _hamming_distance_hex(h0, h1) if h0 and h1 else 10**9
            hash_progress_ok = bool(hash_delta != 10**9 and hash_delta >= 6)

            confirmed = bool(match_ok and (progress_ok or hash_progress_ok))
            payload = {
                "targetSong": target_song,
                "targetNorm": target_norm,
                "confirmed": confirmed,
                "matchOk": match_ok,
                "progressOk": progress_ok,
                "hashProgressOk": hash_progress_ok,
                "hashDelta": hash_delta,
                "samples": [
                    {"text": text0, "seconds": t0, "capture": captures[0] if captures else None},
                    {"text": text1, "seconds": t1, "capture": captures[1] if len(captures) > 1 else None},
                ],
            }
            debug_info.setdefault("playbackCheck", []).append(payload)
            return payload

        def _roi_px(roi: tuple[float, float, float, float], *, iw: float, ih: float) -> tuple[float, float, float, float]:
            rx, ry, rw, rh = roi
            return (float(rx) * iw, float(ry) * ih, float(rw) * iw, float(rh) * ih)

        def _box_center_in_roi(box: Any, *, roi_px: tuple[float, float, float, float]) -> bool:
            x0, y0, w0, h0 = roi_px
            x1 = x0 + w0
            y1 = y0 + h0
            try:
                cx, cy = box.center()
            except Exception:
                return False
            return (x0 <= float(cx) <= x1) and (y0 <= float(cy) <= y1)

        def _find_best_box(
            boxes: List[Any],
            *,
            target: str,
            roi_px: Optional[tuple[float, float, float, float]],
            min_conf: float,
            match_mode: str = "contains",
            prefer_top_left: bool = False,
        ) -> Optional[Any]:
            t_norm = _norm(target)
            if not t_norm:
                return None

            candidates: list[Any] = []
            for b in boxes:
                try:
                    conf = float(getattr(b, "confidence", 0.0) or 0.0)
                except Exception:
                    conf = 0.0
                if conf < float(min_conf):
                    continue

                if roi_px is not None and not _box_center_in_roi(b, roi_px=roi_px):
                    continue

                text_norm = _norm(str(getattr(b, "text", "") or ""))
                if not text_norm:
                    continue

                ok = (text_norm == t_norm) if match_mode == "exact" else (t_norm in text_norm)
                if not ok:
                    continue

                candidates.append(b)

            if not candidates:
                return None

            if prefer_top_left:
                return min(candidates, key=lambda b: (float(getattr(b, "y", 0.0) or 0.0), float(getattr(b, "x", 0.0) or 0.0)))

            return max(
                candidates,
                key=lambda b: (
                    float(getattr(b, "confidence", 0.0) or 0.0),
                    float(getattr(b, "width", 0.0) or 0.0) * float(getattr(b, "height", 0.0) or 0.0),
                ),
            )

        async def _capture_full(tag: str) -> tuple[Dict[str, Any], List[Any], float, float]:
            cap = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=tag)
            debug_info.setdefault("captures", []).append({"step": tag, "capture": cap})

            path = str(cap.get("screenshotPath") or "")
            iw = float(((cap.get("imageSize") or {}).get("width")) or 0.0)
            ih = float(((cap.get("imageSize") or {}).get("height")) or 0.0)

            boxes: list[Any] = []
            if path:
                # State detection is most sensitive to the small top tabs/search texts.
                # Prefer high-scale OCR on tight ROIs to avoid missing the main tabs.
                try:
                    tabs_words = list(KUGOU_OCR_CLICK_KWARGS.get("ocr_custom_words") or [])
                    tabs_words.extend(["音乐", "艺人", "动态", "歌单", "音频", "歌手", "专辑", "视频", "综合", "单曲", "歌词", "听书"])
                    tabs_words = list(dict.fromkeys([str(x) for x in tabs_words if str(x).strip()]))

                    boxes.extend(
                        await self.ui.ocr_screenshot_advanced(
                            path,
                            roi=KUGOU_ROIS["tabs"],
                            scale=3.2,
                            grayscale=True,
                            accurate=False,
                            custom_words=tabs_words,
                        )
                    )
                    boxes.extend(
                        await self.ui.ocr_screenshot_advanced(
                            path,
                            roi=KUGOU_ROIS["top_search_verify"],
                            scale=3.2,
                            grayscale=True,
                            accurate=False,
                            custom_words=["搜索", "取消", "历史", "历史搜索"],
                        )
                    )
                except Exception as e:
                    debug_info.setdefault("warnings", []).append(f"状态OCR失败（回退全图OCR）：{e}")
                    boxes = await self.ui.ocr_screenshot_advanced(
                        path,
                        roi=KUGOU_ROIS["full"],
                        scale=1.0,
                        grayscale=True,
                        accurate=False,
                        custom_words=KUGOU_OCR_CLICK_KWARGS.get("ocr_custom_words"),
                    )

            return (cap, boxes, iw, ih)

        def _detect_state(boxes: List[Any], *, iw: float, ih: float) -> Dict[str, Any]:
            roi_top = _roi_px(KUGOU_ROIS["top_search_verify"], iw=iw, ih=ih)
            roi_tabs = _roi_px(KUGOU_ROIS["tabs"], iw=iw, ih=ih)

            cancel_box = _find_best_box(boxes, target="取消", roi_px=roi_top, min_conf=detect_min_confidence)
            search_box = _find_best_box(boxes, target="搜索", roi_px=roi_top, min_conf=detect_min_confidence)
            history_full_box = _find_best_box(boxes, target="历史搜索", roi_px=roi_top, min_conf=detect_min_confidence)
            history_split = bool(
                _find_best_box(boxes, target="历史", roi_px=roi_top, min_conf=detect_min_confidence)
                and _find_best_box(boxes, target="搜索", roi_px=roi_top, min_conf=detect_min_confidence)
            )
            has_history = bool(history_full_box is not None or history_split)

            # KuGou UI varies across versions/themes.
            # Current stable indicators on the music home page include these mid-row tabs.
            main_tabs = ["歌单", "音频", "歌手", "专辑", "视频"]
            main_hits = 0
            for t in main_tabs:
                if _find_best_box(boxes, target=t, roi_px=roi_tabs, min_conf=detect_min_confidence):
                    main_hits += 1

            # Search results page typically shows a tabs row that includes "综合"/"单曲".
            results_tabs = ["综合", "单曲", "歌词", "听书", "专辑", "歌单", "视频"]
            results_hits = 0
            for t in results_tabs:
                if _find_best_box(boxes, target=t, roi_px=roi_tabs, min_conf=detect_min_confidence):
                    results_hits += 1

            results_core_hits = 0
            for t in ["综合", "单曲"]:
                if _find_best_box(boxes, target=t, roi_px=roi_tabs, min_conf=detect_min_confidence):
                    results_core_hits += 1

            results_ratio = float(results_hits) / float(len(results_tabs) or 1)
            is_results_page = bool((cancel_box is not None) and (results_core_hits >= 1) and (results_hits >= 2))
            is_search_view = bool((cancel_box is not None) or has_history)

            # OCR may fail to read the placeholder "搜索" due to low contrast.
            # Treat "main shell" as tabs hits >= 2/4 (tolerant), and do NOT require "搜索".
            is_main_shell = bool(main_hits >= 2)
            is_main_ready = bool(is_main_shell and (cancel_box is None) and (not has_history))

            if is_results_page:
                mode = "results_page"
            elif is_search_view:
                mode = "search_view"
            elif is_main_ready:
                mode = "music_main_ready"
            else:
                mode = "unknown"

            return {
                "mode": mode,
                "mainHits": int(main_hits),
                "mainCount": int(len(main_tabs)),
                "resultsHits": int(results_hits),
                "resultsCount": int(len(results_tabs)),
                "resultsRatio": results_ratio,
                "hasCancel": bool(cancel_box is not None),
                "hasSearch": bool(search_box is not None),
                "hasHistory": bool(has_history),
            }

        def _to_screen_point(cap: Dict[str, Any], *, image_x: float, image_y: float) -> tuple[float, float]:
            """Convert window screenshot image point (origin top-left) to screen point."""

            try:
                sx, sy = self.ui._to_screen_point_from_window_image_point(  # type: ignore[attr-defined]
                    float(image_x),
                    float(image_y),
                    window_bounds=cap.get("windowBounds") or {},
                    image_size=cap.get("imageSize") or {},
                )
                return (float(sx), float(sy))
            except Exception as e:
                raise RuntimeError(f"无法将截图坐标转换为屏幕坐标：{e}")

        async def _click_box_center(cap: Dict[str, Any], box: Any, *, step: str) -> None:
            try:
                cx, cy = box.center()
            except Exception:
                raise RuntimeError("无法获取 OCR box 中心点")

            sx, sy = _to_screen_point(cap, image_x=float(cx), image_y=float(cy))

            ocr_payload = {
                "text": str(getattr(box, "text", "") or ""),
                "x": float(getattr(box, "x", 0.0) or 0.0),
                "y": float(getattr(box, "y", 0.0) or 0.0),
                "width": float(getattr(box, "width", 0.0) or 0.0),
                "height": float(getattr(box, "height", 0.0) or 0.0),
            }
            click_debug = await _click_screen_point(
                cap,
                sx=float(sx),
                sy=float(sy),
                step=step,
                clicks=1,
                ocr_box=ocr_payload,
            )

            debug_info.setdefault("clicks", []).append(
                {
                    "step": step,
                    "imagePoint": {"x": float(cx), "y": float(cy)},
                    "screenPoint": {"x": float(sx), "y": float(sy)},
                    "text": ocr_payload["text"],
                    "ocrBox": ocr_payload,
                    "clickDebug": click_debug,
                    "captureMeta": {
                        "windowBounds": cap.get("windowBounds") or {},
                        "imageSize": cap.get("imageSize") or {},
                    },
                }
            )

        async def _click_anchor_offset(cap: Dict[str, Any], *, anchor_box: Any, dx: float, dy: float, step: str) -> None:
            try:
                cx, cy = anchor_box.center()
            except Exception:
                raise RuntimeError("无法获取 anchor box 中心点")

            sx, sy = _to_screen_point(cap, image_x=float(cx) + float(dx), image_y=float(cy) + float(dy))

            anchor_payload = {
                "text": str(getattr(anchor_box, "text", "") or ""),
                "x": float(getattr(anchor_box, "x", 0.0) or 0.0),
                "y": float(getattr(anchor_box, "y", 0.0) or 0.0),
                "width": float(getattr(anchor_box, "width", 0.0) or 0.0),
                "height": float(getattr(anchor_box, "height", 0.0) or 0.0),
            }
            click_debug = await _click_screen_point(
                cap,
                sx=float(sx),
                sy=float(sy),
                step=step,
                clicks=1,
                ocr_box=anchor_payload,
            )

            debug_info.setdefault("clicks", []).append(
                {
                    "step": step,
                    "imagePoint": {"x": float(cx) + float(dx), "y": float(cy) + float(dy)},
                    "screenPoint": {"x": float(sx), "y": float(sy)},
                    "anchor": anchor_payload["text"],
                    "anchorCenter": {"x": float(cx), "y": float(cy)},
                    "offset": {"dx": float(dx), "dy": float(dy)},
                    "anchorBox": anchor_payload,
                    "clickDebug": click_debug,
                    "captureMeta": {
                        "windowBounds": cap.get("windowBounds") or {},
                        "imageSize": cap.get("imageSize") or {},
                    },
                }
            )

        async def _click_top_back_by_title_anchor(
            cap: Dict[str, Any],
            *,
            screenshot_path: str,
            roi: tuple[float, float, float, float],
            step: str,
        ) -> bool:
            """Try to click the top-left back button by anchoring on the page title text.

            Many KuGou back buttons are icons (OCR doesn't recognize). But page titles like "分类" are OCR-friendly.
            We click left to the title as a deterministic OCR-anchored action.
            """

            if not screenshot_path:
                return False

            boxes_roi = await self.ui.ocr_screenshot_advanced(
                screenshot_path,
                roi=roi,
                scale=1.0,
                grayscale=True,
                accurate=False,
                custom_words=KUGOU_OCR_CLICK_KWARGS.get("ocr_custom_words"),
            )
            candidates: list[Any] = []
            for b in boxes_roi:
                try:
                    conf = float(getattr(b, "confidence", 0.0) or 0.0)
                except Exception:
                    conf = 0.0
                if conf < detect_min_confidence:
                    continue

                text_norm = _norm(str(getattr(b, "text", "") or ""))
                if not text_norm:
                    continue

                candidates.append(b)

            if not candidates:
                return False

            title = min(
                candidates,
                key=lambda b: (
                    float(getattr(b, "y", 0.0) or 0.0),
                    float(getattr(b, "x", 0.0) or 0.0),
                    -(float(getattr(b, "confidence", 0.0) or 0.0)),
                ),
            )

            dx = -max(80.0, float(getattr(title, "width", 0.0) or 0.0) * 0.8)
            await _click_anchor_offset(cap, anchor_box=title, dx=dx, dy=0.0, step=step)
            return True

        async def _close_overlay_panel() -> None:
            cap, boxes, iw, ih = await _capture_full("kugou_overlay_panel")
            if float(iw) <= 1 or float(ih) <= 1:
                raise RuntimeError("无法读取截图尺寸，无法关闭遮挡窗口")

            path = str(cap.get("screenshotPath") or "")
            if not path:
                raise RuntimeError("无法获取截图路径，无法关闭遮挡窗口")

            panel_rois = [
                (0.42, 0.0, 0.58, 0.18),  # right small panel top
                (0.18, 0.0, 0.82, 0.18),  # large content panel top
            ]

            for idx, roi in enumerate(panel_rois):
                ok = await _click_top_back_by_title_anchor(
                    cap,
                    screenshot_path=path,
                    roi=roi,
                    step=f"overlay_back_by_title_{idx}",
                )
                if ok:
                    await asyncio.sleep(0.35)
                    return

            debug_info.setdefault("warnings", []).append("遮挡窗口关闭失败：未能在遮挡窗口顶部 ROI 内识别到可用标题锚点")
            raise RuntimeError("遮挡窗口存在，但未能通过标题锚点定位返回按钮")

        async def _click_sidebar_music(*, tag: str, step_name: str) -> None:
            cap0 = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=tag)
            debug_info.setdefault("captures", []).append({"step": tag, "capture": cap0})

            path0 = str(cap0.get("screenshotPath") or "")
            if not path0:
                raise RuntimeError("无法获取酷狗窗口截图路径")

            boxes_music = await self.ui.ocr_screenshot_advanced(
                path0,
                # Use a composed ROI for the top "音乐" entry to reduce accidental hits.
                roi=KUGOU_ROIS["sidebar_music"],
                scale=3.2,
                grayscale=True,
                accurate=False,
                custom_words=["音乐"],
            )

            def _is_music_box(b: Any) -> bool:
                txt = _norm(str(getattr(b, "text", "") or ""))
                return "音乐" in txt

            music_boxes_tight = [b for b in boxes_music if _is_music_box(b)]
            if music_boxes_tight:
                # Prefer the top-most "音乐" label inside the tight ROI.
                music_box = min(
                    music_boxes_tight,
                    key=lambda b: (
                        float(getattr(b, "y", 0.0) or 0.0),
                        float(getattr(b, "x", 0.0) or 0.0),
                        -(float(getattr(b, "confidence", 0.0) or 0.0)),
                    ),
                )
                await _click_box_center(cap0, music_box, step=str(step_name or "kugou_sidebar_music"))
                return

            boxes0 = await self.ui.ocr_screenshot_advanced(
                path0,
                # Use a wider sidebar ROI to always include the three main entries.
                roi=KUGOU_ROIS["sidebar"],
                scale=3.2,
                grayscale=True,
                accurate=False,
                custom_words=["音乐", "视频", "我的"],
            )

            iw0 = float(((cap0.get("imageSize") or {}).get("width")) or 0.0)
            ih0 = float(((cap0.get("imageSize") or {}).get("height")) or 0.0)
            if iw0 <= 1 or ih0 <= 1:
                raise RuntimeError("无法读取截图尺寸")

            def _is_label_box(b: Any) -> bool:
                txt = _norm(str(getattr(b, "text", "") or ""))
                return any(k in txt for k in ("音乐", "视频", "我的"))

            label_boxes = [b for b in boxes0 if _is_label_box(b)]
            if not label_boxes:
                preview = [str(getattr(b, "text", "") or "") for b in boxes0[:12]]
                raise RuntimeError(f"侧边栏 ROI 未识别到入口文本（preview={preview}）")

            music_boxes = [b for b in label_boxes if "音乐" in _norm(str(getattr(b, "text", "") or ""))]
            video_boxes = [b for b in label_boxes if "视频" in _norm(str(getattr(b, "text", "") or ""))]
            my_boxes = [b for b in label_boxes if "我的" in _norm(str(getattr(b, "text", "") or ""))]

            step_tag = str(step_name or "kugou_sidebar_music")

            if music_boxes:
                # Prefer the top-most "音乐" label.
                music_box = min(
                    music_boxes,
                    key=lambda b: (
                        float(getattr(b, "y", 0.0) or 0.0),
                        float(getattr(b, "x", 0.0) or 0.0),
                        -(float(getattr(b, "confidence", 0.0) or 0.0)),
                    ),
                )
                await _click_box_center(cap0, music_box, step=step_tag)
                return

            # If OCR missed the "音乐" label, anchor on "视频"/"我的" and click upward to the expected slot.
            if video_boxes:
                vb = min(video_boxes, key=lambda b: (float(getattr(b, "y", 0.0) or 0.0), float(getattr(b, "x", 0.0) or 0.0)))
                dy = max(140.0, float(getattr(vb, "height", 0.0) or 0.0) * 6.0)
                await _click_anchor_offset(cap0, anchor_box=vb, dx=0.0, dy=-dy, step=f"{step_tag}_above_video")
                return

            if my_boxes:
                mb = min(my_boxes, key=lambda b: (float(getattr(b, "y", 0.0) or 0.0), float(getattr(b, "x", 0.0) or 0.0)))
                dy = max(280.0, float(getattr(mb, "height", 0.0) or 0.0) * 12.0)
                await _click_anchor_offset(cap0, anchor_box=mb, dx=0.0, dy=-dy, step=f"{step_tag}_above_my")
                return

            preview = [str(getattr(b, "text", "") or "") for b in label_boxes[:12]]
            raise RuntimeError(f"侧边栏入口识别异常（preview={preview}）")

        # 1) Always click sidebar "音乐" first.
        try:
            await _click_sidebar_music(tag="kugou_sidebar_music", step_name="kugou_sidebar_music")
            await asyncio.sleep(0.25)
        except Exception as e:
            debug_info.setdefault("warnings", []).append(f"侧边栏点击‘音乐’失败（继续）：{e}")
        _dump_debug_info("after_sidebar_music")

        # 2) Converge to a state where we can input query (search view or results page).
        cap: Dict[str, Any]
        boxes: List[Any]
        iw = 0.0
        ih = 0.0
        enter_search_fallback_tries = 0
        for step in range(6):
            cap, boxes, iw, ih = await _capture_full(f"kugou_flow_state_{step}")
            state = _detect_state(boxes, iw=iw, ih=ih)
            debug_info.setdefault("flow", []).append({"step": step, "state": state})
            _dump_debug_info(f"state_{step}")

            if state.get("mode") in {"search_view", "results_page"}:
                break

            if state.get("mode") == "music_main_ready":
                # Enter search view.
                # - Prefer OCR on a tight `search_bar` ROI (works better for low-contrast placeholder "搜索").
                # - Click with a right-shift so we land inside the input area (avoid the icon prefix).
                # - Validate that we entered search view (出现“取消”或“历史搜索”) before moving on.

                screenshot_path = str(cap.get("screenshotPath") or "")

                async def _ocr_roi(
                    roi: tuple[float, float, float, float],
                    *,
                    scale: float,
                    grayscale: bool,
                ) -> list[Any]:
                    if not screenshot_path:
                        return []
                    return await self.ui.ocr_screenshot_advanced(
                        screenshot_path,
                        roi=roi,
                        scale=float(scale),
                        grayscale=bool(grayscale),
                        accurate=False,
                        custom_words=["搜索", "取消", "历史搜索"],
                    )

                def _has_text(boxes_any: list[Any], target: str) -> bool:
                    t = _norm(target)
                    for b in boxes_any:
                        if t and t in _norm(str(getattr(b, "text", "") or "")):
                            return True
                    return False

                async def _verify_search_view(*, tag: str) -> tuple[bool, Dict[str, Any], list[Any]]:
                    cap2 = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=tag)
                    path2 = str(cap2.get("screenshotPath") or "")
                    if not path2:
                        return (False, cap2, [])

                    # Prefer a tight ROI check; seeing "取消" or "历史搜索" is strong evidence of search view.
                    boxes2 = await self.ui.ocr_screenshot_advanced(
                        path2,
                        roi=KUGOU_ROIS["top_search_verify"],
                        scale=3.2,
                        grayscale=True,
                        accurate=False,
                        custom_words=["搜索", "取消", "历史", "历史搜索"],
                    )
                    ok = (
                        _has_text(boxes2, "取消")
                        or _has_text(boxes2, "历史搜索")
                        or (_has_text(boxes2, "历史") and _has_text(boxes2, "搜索"))
                    )
                    debug_info.setdefault("searchEntryVerify", []).append(
                        {
                            "tag": tag,
                            "ok": bool(ok),
                            "preview": [str(getattr(b, "text", "") or "") for b in boxes2[:10]],
                        }
                    )
                    return (bool(ok), cap2, boxes2)

                # 1) OCR in search_bar ROI
                search_bar_boxes: list[Any] = []
                if screenshot_path:
                    for g in [True, False]:
                        search_bar_boxes = await _ocr_roi(KUGOU_ROIS["search_bar"], scale=3.2, grayscale=g)
                        if _has_text(search_bar_boxes, "搜索"):
                            break

                picked = None
                if search_bar_boxes:
                    candidates = [b for b in search_bar_boxes if "搜索" in _norm(str(getattr(b, "text", "") or ""))]
                    if candidates:
                        picked = max(
                            candidates,
                            key=lambda b: (
                                float(getattr(b, "confidence", 0.0) or 0.0),
                                float(getattr(b, "width", 0.0) or 0.0) * float(getattr(b, "height", 0.0) or 0.0),
                            ),
                        )

                if picked is not None:
                    try:
                        cx, cy = picked.center()
                    except Exception:
                        cx, cy = (float(getattr(picked, "x", 0.0) or 0.0), float(getattr(picked, "y", 0.0) or 0.0))

                    # Shift right into input area to avoid clicking the icon prefix.
                    dx = max(16.0, float(getattr(picked, "width", 0.0) or 0.0) * 0.35)
                    sx, sy = _to_screen_point(cap, image_x=float(cx) + float(dx), image_y=float(cy))

                    ocr_payload = {
                        "text": str(getattr(picked, "text", "") or ""),
                        "x": float(getattr(picked, "x", 0.0) or 0.0),
                        "y": float(getattr(picked, "y", 0.0) or 0.0),
                        "width": float(getattr(picked, "width", 0.0) or 0.0),
                        "height": float(getattr(picked, "height", 0.0) or 0.0),
                    }
                    click_debug = await _click_screen_point(
                        cap,
                        sx=float(sx),
                        sy=float(sy),
                        step=f"enter_search_search_bar_{step}",
                        clicks=2,
                        ocr_box=ocr_payload,
                    )

                    debug_info.setdefault("clicks", []).append(
                        {
                            "step": "enter_search_search_bar",
                            "imagePoint": {"x": float(cx) + float(dx), "y": float(cy)},
                            "screenPoint": {"x": float(sx), "y": float(sy)},
                            "anchor": ocr_payload["text"],
                            "anchorCenter": {"x": float(cx), "y": float(cy)},
                            "offset": {"dx": float(dx), "dy": 0.0},
                            "ocrBox": ocr_payload,
                            "clickDebug": click_debug,
                        }
                    )
                    await asyncio.sleep(0.35)

                    ok, cap2, boxes2 = await _verify_search_view(tag=f"kugou_enter_search_verify_{step}")
                    if ok:
                        cap = cap2
                        boxes = boxes2
                        iw = float(((cap.get("imageSize") or {}).get("width")) or 0.0)
                        ih = float(((cap.get("imageSize") or {}).get("height")) or 0.0)
                        break

                # 2) If OCR cannot see "搜索" text (icon-only UI), click the global search icon (top-right)
                # deterministically and verify by "取消/历史搜索".
                for idx, (x_norm, y_norm) in enumerate(
                    [
                        (0.955, 0.065),
                        (0.935, 0.065),
                        (0.955, 0.095),
                    ]
                ):
                    image_x = float(iw) * float(x_norm)
                    image_y = float(ih) * float(y_norm)
                    sx, sy = _to_screen_point(cap, image_x=image_x, image_y=image_y)
                    click_debug = await _click_screen_point(
                        cap,
                        sx=float(sx),
                        sy=float(sy),
                        step=f"enter_search_icon_{step}_{idx}",
                        clicks=1,
                        ocr_box=None,
                    )
                    debug_info.setdefault("clicks", []).append(
                        {
                            "step": "enter_search_icon",
                            "idx": int(idx),
                            "imagePoint": {"x": image_x, "y": image_y},
                            "screenPoint": {"x": float(sx), "y": float(sy)},
                            "note": "top-right search icon click",
                            "clickDebug": click_debug,
                        }
                    )
                    await asyncio.sleep(0.30)
                    ok, cap2, boxes2 = await _verify_search_view(tag=f"kugou_enter_search_verify_icon_{step}_{idx}")
                    if ok:
                        cap = cap2
                        boxes = boxes2
                        iw = float(((cap.get("imageSize") or {}).get("width")) or 0.0)
                        ih = float(((cap.get("imageSize") or {}).get("height")) or 0.0)
                        break
                else:
                    # 3) Deterministic fallback click inside search_bar ROI (some versions expose a text search bar).
                    enter_search_fallback_tries += 1
                    x_norm = float(KUGOU_ROIS["search_bar"][0]) + float(KUGOU_ROIS["search_bar"][2]) * 0.78
                    y_norm = float(KUGOU_ROIS["search_bar"][1]) + float(KUGOU_ROIS["search_bar"][3]) * 0.55
                    image_x = float(iw) * x_norm
                    image_y = float(ih) * y_norm
                    sx, sy = _to_screen_point(cap, image_x=image_x, image_y=image_y)
                    click_debug = await _click_screen_point(
                        cap,
                        sx=float(sx),
                        sy=float(sy),
                        step=f"enter_search_fallback_{step}_{enter_search_fallback_tries}",
                        clicks=2,
                        ocr_box=None,
                    )

                    debug_info.setdefault("clicks", []).append(
                        {
                            "step": "enter_search_fallback",
                            "try": int(enter_search_fallback_tries),
                            "imagePoint": {"x": image_x, "y": image_y},
                            "screenPoint": {"x": float(sx), "y": float(sy)},
                            "note": "search_bar ROI fallback click",
                            "clickDebug": click_debug,
                        }
                    )
                    await asyncio.sleep(0.35)

                    ok, cap2, boxes2 = await _verify_search_view(tag=f"kugou_enter_search_verify_fallback_{step}")
                    if ok:
                        cap = cap2
                        boxes = boxes2
                        iw = float(((cap.get("imageSize") or {}).get("width")) or 0.0)
                        ih = float(((cap.get("imageSize") or {}).get("height")) or 0.0)
                        break

                    # If we still cannot enter search view, try closing possible overlay panel and continue.
                    if enter_search_fallback_tries >= 2:
                        try:
                            await _close_overlay_panel()
                        except Exception as e:
                            debug_info.setdefault("warnings", []).append(f"疑似遮挡层关闭失败（忽略继续）：{e}")

                    continue

                continue

            # Unknown state: try top-back (subpage like "分类"), then re-check; if still not resolved, click sidebar music.
            try:
                path = str(cap.get("screenshotPath") or "")
                did_back = await _click_top_back_by_title_anchor(
                    cap,
                    screenshot_path=path,
                    roi=(0.18, 0.0, 0.82, 0.12),
                    step=f"top_back_unknown_{step}",
                )
                if did_back:
                    await asyncio.sleep(0.35)
                    continue
            except Exception as e:
                debug_info.setdefault("warnings", []).append(f"子页返回尝试失败（idx={step}，忽略继续）：{e}")

            try:
                await _click_sidebar_music(
                    tag=f"kugou_sidebar_music_retry_{step}",
                    step_name=f"kugou_sidebar_music_retry_{step}",
                )
                await asyncio.sleep(0.25)
            except Exception as e:
                debug_info.setdefault("warnings", []).append(f"重试点击‘音乐’失败（idx={step}）：{e}")
                await asyncio.sleep(0.25)
        else:
            _dump_debug_info("error_converge_failed", error="未能收敛到可搜索界面")
            raise RuntimeError("未能收敛到可搜索界面（search view / results page）")

        # 3) Focus search input (anchor on "搜索" or "取消") then submit query.
        if float(iw) <= 1 or float(ih) <= 1:
            raise RuntimeError("无法读取截图尺寸，无法执行搜索")

        roi_top = _roi_px(KUGOU_ROIS["top_search"], iw=iw, ih=ih)
        cancel_box = _find_best_box(boxes, target="取消", roi_px=roi_top, min_conf=detect_min_confidence)
        search_box = _find_best_box(boxes, target="搜索", roi_px=roi_top, min_conf=detect_min_confidence, prefer_top_left=True)

        if search_box is not None:
            await _click_box_center(cap, search_box, step="focus_search")
        elif cancel_box is not None:
            # Click a point to the left of "取消" to focus the input field.
            await _click_anchor_offset(cap, anchor_box=cancel_box, dx=-160.0, dy=0.0, step="focus_input_left_of_cancel")
        else:
            # Placeholder "搜索" may not be OCR-detectable. Click inside the search bar ROI as fallback.
            x_norm = float(KUGOU_ROIS["top_search"][0]) + float(KUGOU_ROIS["top_search"][2]) * 0.58
            y_norm = float(KUGOU_ROIS["top_search"][1]) + float(KUGOU_ROIS["top_search"][3]) * 0.45
            image_x = float(iw) * x_norm
            image_y = float(ih) * y_norm
            sx, sy = _to_screen_point(cap, image_x=image_x, image_y=image_y)
            click_debug = await _click_screen_point(
                cap,
                sx=float(sx),
                sy=float(sy),
                step="focus_search_fallback",
                clicks=1,
                ocr_box=None,
            )
            debug_info.setdefault("clicks", []).append(
                {
                    "step": "focus_search_fallback",
                    "imagePoint": {"x": image_x, "y": image_y},
                    "screenPoint": {"x": float(sx), "y": float(sy)},
                    "note": "top_search ROI fallback click",
                    "clickDebug": click_debug,
                }
            )

        await asyncio.sleep(0.18)
        try:
            await self.ui.hotkey("a", modifiers=["command down"])
            await self.ui.key_code(51)
        except Exception as e:
            debug_info.setdefault("warnings", []).append(f"清空输入框失败（忽略）：{e}")

        _pbcopy(query)
        await asyncio.sleep(0.05)
        try:
            await self.ui.hotkey("v", modifiers=["command down"])
        except Exception as e:
            debug_info.setdefault("warnings", []).append(f"粘贴搜索词失败（忽略）：{e}")

        await asyncio.sleep(0.12)
        await self.ui.key_code(36)  # Enter
        await asyncio.sleep(0.85)

        # 4) Verify results page.
        cap, boxes, iw, ih = await _capture_full("kugou_after_submit")
        state = _detect_state(boxes, iw=iw, ih=ih)
        debug_info.setdefault("flow", []).append({"step": "after_submit", "state": state})
        _dump_debug_info("after_submit")
        if state.get("mode") != "results_page":
            _dump_debug_info("error_not_results_page", error=str(state))
            raise RuntimeError(f"未能进入搜索结果页：{state}")

        # 5) Click "单曲" tab.
        roi_tabs = _roi_px(KUGOU_ROIS["tabs"], iw=iw, ih=ih)
        tab_song = _find_best_box(boxes, target="单曲", roi_px=roi_tabs, min_conf=detect_min_confidence, prefer_top_left=True)
        if tab_song is None:
            raise RuntimeError("已进入结果页，但未识别到‘单曲’入口")
        await _click_box_center(cap, tab_song, step="tab_song")
        await asyncio.sleep(0.35)

        # 6) Click the target song title (single click), then verify playback via bottom bar.
        cap, _, iw, ih = await _capture_full("kugou_song_list")
        path = str(cap.get("screenshotPath") or "")
        if not path:
            raise RuntimeError("无法获取截图路径，无法在结果列表中定位歌曲")

        song_key = _target_song_from_query(query)
        song_norm = _norm(song_key)
        if not song_norm:
            raise RuntimeError("搜索词为空，无法定位目标歌曲")

        def _dump_song_list_ocr_evidence(
            *,
            stage: str,
            screenshot_path: str,
            roi_norm: tuple[float, float, float, float],
            boxes: List[Any],
        ) -> Dict[str, str]:
            """Dump OCR evidence (boxes + ROI crop) for post-mortem analysis.

            This is a best-effort helper. Failures here should never mask the main error.
            """

            out: Dict[str, str] = {}
            try:
                ts = int(time.time() * 1000)
                out_dir = _workflow_debug_dir()

                iw0 = float(((cap.get("imageSize") or {}).get("width")) or 0.0)
                ih0 = float(((cap.get("imageSize") or {}).get("height")) or 0.0)

                rx, ry, rw, rh = roi_norm
                roi_px = {
                    "x": int(round(float(rx) * iw0)) if iw0 > 0 else 0,
                    "y": int(round(float(ry) * ih0)) if ih0 > 0 else 0,
                    "width": int(round(float(rw) * iw0)) if iw0 > 0 else 0,
                    "height": int(round(float(rh) * ih0)) if ih0 > 0 else 0,
                }

                # 1) Dump boxes JSON (sorted by confidence, top N for readability).
                top = sorted(
                    boxes,
                    key=lambda b: float(getattr(b, "confidence", 0.0) or 0.0),
                    reverse=True,
                )[:160]
                boxes_path = out_dir / f"kugou_song_list_ocr_boxes_{stage}_{ts}.json"
                payload = {
                    "timestampMs": ts,
                    "stage": str(stage or ""),
                    "screenshotPath": str(screenshot_path),
                    "roiNormalized": {
                        "x": float(rx),
                        "y": float(ry),
                        "width": float(rw),
                        "height": float(rh),
                    },
                    "roiPixels": roi_px,
                    "captureMeta": {
                        "windowBounds": cap.get("windowBounds") or {},
                        "imageSize": cap.get("imageSize") or {},
                    },
                    "ocr": {
                        "engine": "Vision",
                        "scale": 2.4,
                        "grayscale": True,
                        "accurate": False,
                    },
                    "boxesTotal": int(len(boxes)),
                    "previewTop12": [str(getattr(b, "text", "") or "") for b in top[:12]],
                    "boxes": [
                        {
                            "text": str(getattr(b, "text", "") or ""),
                            "confidence": float(getattr(b, "confidence", 0.0) or 0.0),
                            "x": float(getattr(b, "x", 0.0) or 0.0),
                            "y": float(getattr(b, "y", 0.0) or 0.0),
                            "width": float(getattr(b, "width", 0.0) or 0.0),
                            "height": float(getattr(b, "height", 0.0) or 0.0),
                        }
                        for b in top
                    ],
                }
                boxes_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                out["ocrBoxesJson"] = str(boxes_path)

                # 2) Dump ROI crop PNG (visual evidence).
                try:
                    h = int(max(1, roi_px.get("height") or 1))
                    w = int(max(1, roi_px.get("width") or 1))
                    y = int(max(0, roi_px.get("y") or 0))
                    x = int(max(0, roi_px.get("x") or 0))
                    crop_path = out_dir / f"kugou_song_list_roi_crop_{stage}_{ts}.png"
                    subprocess.run(
                        [
                            "sips",
                            "-c",
                            str(h),
                            str(w),
                            "--cropOffset",
                            str(y),
                            str(x),
                            str(screenshot_path),
                            "-o",
                            str(crop_path),
                        ],
                        capture_output=True,
                        text=True,
                    )
                    if crop_path.exists():
                        out["roiCropPng"] = str(crop_path)
                except Exception as e:
                    debug_info.setdefault("warnings", []).append(f"ROI 裁剪图落盘失败（忽略）：{e}")

                return out
            except Exception as e:
                debug_info.setdefault("warnings", []).append(f"单曲列表 OCR 证据落盘失败（忽略）：{e}")
                return out

        # OCR the result list region; use a dedicated ROI here (do NOT change global KUGOU_ROIS).
        # Reason: global `result_list_top` is tuned for other steps and may clip the left part of the title.
        song_list_roi = (0.06, 0.20, 0.90, 0.50)
        list_boxes = await self.ui.ocr_screenshot_advanced(
            path,
            roi=song_list_roi,
            scale=2.4,
            grayscale=True,
            accurate=False,
            custom_words=[song_key, "VIP", "MV", "播放", "暂停"],
        )

        best = None
        best_key = None
        for b in list_boxes:
            text = str(getattr(b, "text", "") or "").strip()
            if not text:
                continue

            t_norm = _norm(text)
            if not t_norm:
                continue

            # Avoid picking the header line like "歌手：xxx" unless it also matches the song key.
            if ("歌手" in t_norm) and (song_norm not in t_norm):
                continue

            try:
                conf = float(getattr(b, "confidence", 0.0) or 0.0)
            except Exception:
                conf = 0.0

            sim = difflib.SequenceMatcher(None, song_norm, t_norm).ratio() if song_norm else 0.0
            hit = bool(song_norm in t_norm or sim >= 0.6)
            if not hit:
                continue

            try:
                y = float(getattr(b, "y", 0.0) or 0.0)
            except Exception:
                y = 0.0

            # Prefer the first matching row (top-most), then higher confidence/similarity.
            key = (y, -conf, -sim)
            if best_key is None or key < best_key:
                best_key = key
                best = b

        if best is None:
            dumped = _dump_song_list_ocr_evidence(
                stage="target_song_not_found",
                screenshot_path=path,
                roi_norm=song_list_roi,
                boxes=list_boxes,
            )
            preview = [str(getattr(b, "text", "") or "") for b in sorted(list_boxes, key=lambda x: float(getattr(x, "confidence", 0.0) or 0.0), reverse=True)[:20]]
            msg = f"未能在单曲列表页识别到目标歌曲：{song_key}（preview={preview}）"
            if dumped:
                msg += f"（evidence={dumped}）"
            raise RuntimeError(msg)

        click_debug = await _click_box_center(cap, best, step="play_song_title")
        debug_info.setdefault("clicks", []).append(
            {
                "step": "play_song_title",
                "songKey": song_key,
                "matchedText": str(getattr(best, "text", "") or ""),
                "clickDebug": click_debug,
            }
        )

        await asyncio.sleep(0.6)
        pb = await _check_playback(tag_prefix="kugou_play_check_post_v2")
        if bool((pb or {}).get("confirmed")) is not True:
            _dump_debug_info("error_playback_not_confirmed", error=str(pb))
            raise RuntimeError(f"已点击歌曲名但未能确认播放成功：{pb}")

        _dump_debug_info("success")
        return {"message": f"已在酷狗按 OCR 工作流搜索并播放：{query}", "debug": debug_info if debug else None}

    async def _best_effort_click_any(
        self,
        candidates: List[str],
        *,
        tag: str,
        dry_run: bool,
        debug: dict[str, Any],
        min_confidence: float = 0.75,
        click_text_kwargs: Optional[dict[str, Any]] = None,
        allow_fail: bool = False,
    ) -> None:
        last_error: Optional[Exception] = None
        for c in candidates:
            try:
                info = await self.ui.click_text(
                    c,
                    tag=tag,
                    clicks=1,
                    window_owner_names=KUGOU_APP_NAMES,
                    min_confidence=float(min_confidence),
                    dry_run=dry_run,
                    **(click_text_kwargs or {}),
                )
                debug.setdefault("clicks", []).append({"text": c, "result": info})
                return
            except Exception as e:
                last_error = e
                continue

        if allow_fail:
            debug.setdefault("warnings", []).append(f"未找到可点击文本：{candidates}")
            return

        raise RuntimeError(str(last_error) if last_error else "未找到可点击目标")

    @staticmethod
    def _pick_song_like_boxes(boxes: List[Any], *, image_height: float = 0.0) -> List[Any]:
        ignore = {
            "播放",
            "暂停",
            "我的",
            "收藏",
            "歌单",
            "下载",
            "搜索",
            "推荐",
            "首页",
            "听书",
            "直播",
        }
        out = []
        header_cut = float(image_height) * 0.12 if float(image_height) > 1 else 0.0
        for b in boxes:
            text = str(getattr(b, "text", "") or "").strip()
            if not text:
                continue
            if text in ignore:
                continue
            if len(text) < 2 or len(text) > 32:
                continue

            if header_cut > 1:
                try:
                    y = float(getattr(b, "y", 0.0) or 0.0)
                except Exception:
                    y = 0.0
                if y < header_cut:
                    # Avoid clicking headers/search box in the top region.
                    continue

            out.append(b)
        return out

