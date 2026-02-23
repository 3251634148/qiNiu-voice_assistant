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
    """把文本写入剪贴板。

    中文搜索词用"粘贴"方式更稳，避免输入法弹窗干扰。
    """

    subprocess.run(["pbcopy"], input=str(text), text=True, check=False)


def _sips_bmp_bytes(image_path: str, *, max_size: int = 96) -> bytes:
    """用 macOS 的 `sips` 把图片转成 BMP 字节。

    这里不引入 Pillow 等第三方依赖，直接用系统自带的 `sips` 输出 BMP。
    """

    src = str(image_path or "").strip()
    if not src:
        return b""

    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".bmp", delete=False) as f:
            tmp_path = f.name

        # `-Z` 会保持宽高比，并限制最长边尺寸。
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
    """从 BMP 计算一个简单的均值哈希（aHash）。

    这里会先裁剪中间区域再算哈希，尽量不被下面这些 UI 细节干扰：
    - 左侧边栏高亮
    - 顶部搜索框光标闪烁
    - 底部播放进度变化
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

    # `sips` 常输出 32bpp，并使用 BI_BITFIELDS（compression=3）。
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


from backend_py.config_manager import UI_AUTOMATION_CONFIG
from backend_py.services.macos_media_control import MacOSMediaControl
from backend_py.services.macos_ui_automation import MacOSUIAutomation
from backend_py.services.vlm_ui_driver import VlmUiDriver


KUGOU_APP_NAMES = ["酷狗音乐", "KugouMusic", "Kugou Music"]


def _compose_roi(
    base: tuple[float, float, float, float],
    sub: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """在 base ROI 内组合一个子 ROI（归一化坐标，左上为原点）。"""
    bx, by, bw, bh = base
    sx, sy, sw, sh = sub
    return (bx + sx * bw, by + sy * bh, bw * sw, bh * sh)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _parse_roi_csv(value: str) -> Optional[tuple[float, float, float, float]]:
    """解析 `x,y,w,h` 形式的 ROI 字符串。

    - 允许空白
    - 解析失败返回 None
    - 会做 0..1 范围裁剪，且保证 w/h 不越界
    """

    raw = str(value or "").strip()
    if not raw:
        return None

    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) != 4:
        return None

    try:
        x = _clamp01(float(parts[0]))
        y = _clamp01(float(parts[1]))
        w = _clamp01(float(parts[2]))
        h = _clamp01(float(parts[3]))
    except Exception:
        return None

    w = max(0.0, min(1.0 - x, w))
    h = max(0.0, min(1.0 - y, h))
    if w <= 0.0 or h <= 0.0:
        return None

    return (x, y, w, h)


def _roi_from_env(env_name: str, *, default: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """从环境变量读取 ROI 覆盖值。

    用途：酷狗 UI 主题/版本升级时，允许不改代码快速调整 ROI。

    例：
        export KUGOU_RECOMMEND_TABS_STRICT_ROI="0.10,0.03,0.70,0.06"
    """

    parsed = _parse_roi_csv(os.environ.get(env_name, ""))
    return parsed if parsed else tuple(float(x) for x in default)


# 酷狗窗口截图用的归一化 ROI（左上为原点）。
KUGOU_ROIS: dict[str, tuple[float, float, float, float]] = {
    "full": (0.0, 0.0, 1.0, 1.0),
    # 侧边栏 ROI 尽量收紧，避免在内容区匹配到"热门的音乐"这种词。
    "sidebar": (0.0, 0.18, 0.12, 0.72),
    # 侧边栏里顶部"音乐"入口的小 ROI（相对 sidebar ROI）。
    "sidebar_music_sub": (0.0, 0.08, 1.0, 0.38),
    # 点击侧边栏"音乐"入口用的 ROI：只覆盖这块区域，避免点到下面的"视频"。
    "sidebar_music": _compose_roi((0.0, 0.18, 0.12, 0.72), (0.0, 0.08, 1.0, 0.38)),
    # 注意：这个 ROI 用于点击定位，尽量别随便改。
    "top_search": (0.18, 0.00, 0.78, 0.16),
    # 用于判断"是否进入搜索态"的更大 ROI。
    # - 右侧要覆盖到右上角"取消"
    # - 左侧稍微多覆盖一点，避免"历史搜索"被裁掉
    "top_search_verify": (0.12, 0.00, 0.88, 0.20),
    # 搜索框文字区域的更小 ROI（右上）。
    "search_bar": (0.60, 0.00, 0.36, 0.12),
    # 不同版本/主题的 tab 位置不太固定，所以这里用更高一点的 ROI 覆盖两行区域（搜索/结果页状态判定用）。
    "tabs": (0.18, 0.04, 0.78, 0.40),

    # “我的”页顶部 tabs（音乐/艺人/动态）严格 ROI：要求只出现这 3 个词。
    # 该 ROI 由离线扫参脚本 `test_scripts/debug_kugou_strict_tabs_roi_calib.py` 在样本图上得到，避免把内容区（如动态列表）裁入。
    "my_top_tabs_strict": (0.02, 0.04, 0.28, 0.06),

    # 音乐主页二级 tabs（推荐/频道/歌单/歌手）严格 ROI：用于在搜索前强制回到“推荐”子页并关闭可能存在的半栏面板。
    # 注意：该 ROI 受主题/版本影响较大；若严格校验失败，可用扫参脚本更新，或用环境变量直接覆盖：
    #   - KUGOU_RECOMMEND_TABS_STRICT_ROI="0.10,0.03,0.70,0.06"
    "music_recommend_tabs_strict": _roi_from_env(
        "KUGOU_RECOMMEND_TABS_STRICT_ROI",
        default=(0.10, 0.03, 0.70, 0.06),
    ),
    # 旧版 ROI：保留作为兜底候选，避免不同主题/版本下完全失效。
    "music_recommend_tabs_strict_legacy": (0.18, 0.12, 0.56, 0.08),

    "result_list_top": (0.18, 0.20, 0.78, 0.45),
    "bottom_player": (0.00, 0.86, 1.00, 0.14),
}

# 酷狗 OCR 参数预设：主要来自 `test_scripts/debug_ocr_vision_kugou.py` 的一组更稳配置。
# 注意：这里 `accurate=False` 反而更稳（小字号更容易识别）。
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
    """音乐控制相关工具。

    - Apple Music/Spotify：优先走 AppleScript（更稳）
    - 没有可用 API 的播放器（例如酷狗）：走 UI 自动化（OCR + 点击）

    注意：UI 自动化天生不稳定，需要系统授予"辅助功能"等权限。
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

        # 优先尝试 Apple Music，不行再试 Spotify。
        try:
            self._osascript('tell application "Music" to pause')
            return {"message": "已暂停 Apple Music"}
        except Exception:
            pass

        self._osascript('tell application "Spotify" to pause')
        return {"message": "已暂停 Spotify"}

    async def play_music(self, *, source: Optional[str] = None, query: Optional[str] = None) -> Dict[str, Any]:
        """旧入口（低风险）。

        这里只做保守处理：只控制 Apple Music/Spotify。
        需要 UI 自动化的流程请走 `music_ui`。
        """

        if self.system != "darwin":
            raise RuntimeError("当前平台暂不支持播放音乐")

        src = str(source or "").strip().lower() or None
        query_text = str(query or "").strip()

        # 酷狗的低风险默认动作：打开应用 + 触发系统媒体键播放/暂停。
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

        # Apple Music：如果带了 query，就当作歌单名来播。
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

        # auto：如果有 query，先按歌单播放；否则播放 Apple Music；再不行就播 Spotify。
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
        pick_mode: Optional[str] = None,
        debug: bool = False,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """通过 UI 自动化控制播放器。

        Args:
            player: kugou | apple_music
            action: favorites_first | playlist | search
            query: 歌单名 / 搜索关键词
            pick_mode: favorites_first 的选歌方式（first/random，可选）
        """

        if self.system != "darwin" or self.ui is None:
            raise RuntimeError("当前平台暂不支持 UI 自动化音乐控制")

        await self.ui.ensure_accessibility_ready()

        p = str(player or "").strip().lower()
        a = str(action or "").strip().lower()
        q = str(query or "").strip()

        if p not in {"kugou", "apple_music"}:
            raise RuntimeError("player 仅支持 kugou / apple_music")
        if a not in {"favorites_first", "playlist", "search"}:
            raise RuntimeError("action 仅支持 favorites_first / playlist / search")

        if p == "apple_music":
            return await self._apple_music_ui(action=a, query=q, debug=debug, dry_run=dry_run)

        return await self._kugou_ui(action=a, query=q, pick_mode=pick_mode, debug=debug, dry_run=dry_run)

    async def _apple_music_ui(self, *, action: str, query: str, debug: bool, dry_run: bool) -> Dict[str, Any]:
        # 播放歌单用 AppleScript 更稳。
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

            # 先用 AppleScript 搜索播放；失败就退回到 UI 快捷键方式。
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

    async def _kugou_ui(
        self,
        *,
        action: str,
        query: str,
        pick_mode: Optional[str],
        debug: bool,
        dry_run: bool,
    ) -> Dict[str, Any]:
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


        if action == "favorites_first":
            # 新工作流："我的" ->（必要时）顶部"音乐"tab -> 进入"我喜欢" -> 右侧半边栏选歌并单击播放。

            pm = str(pick_mode or "").strip().lower()
            if pm not in {"first", "random"}:
                pm = "first"

            debug_info["mode"] = "favorites_first_v2"
            debug_info["pickMode"] = pm

            def _workflow_debug_dir() -> Path:
                base = Path.home() / "Documents" / "VoiceAssistant" / "ui_debug"
                run_id = str(os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or "").strip()
                out = base / run_id if run_id else base
                out.mkdir(parents=True, exist_ok=True)
                return out

            def _dump_json(tag: str, payload: dict[str, Any]) -> Optional[str]:
                try:
                    ts = int(time.time() * 1000)
                    out_dir = _workflow_debug_dir()
                    out_path = out_dir / f"kugou_favorites_{tag}_{ts}.json"
                    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    debug_info.setdefault("debugDumps", []).append(str(out_path))
                    return str(out_path)
                except Exception as e:
                    debug_info.setdefault("warnings", []).append(f"debug json 落盘失败（忽略）：{e}")
                    return None

            def _norm(value: str) -> str:
                v = str(value or "")
                v = "".join(v.split())
                v = v.replace("\uffff", "").replace("\ufffd", "")
                return v.strip().lower()

            def _to_screen_point(cap: Dict[str, Any], *, image_x: float, image_y: float) -> tuple[float, float]:
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

            async def _capture(tag: str) -> Dict[str, Any]:
                cap = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=tag)
                debug_info.setdefault("captures", []).append({"step": tag, "capture": cap})
                return cap


            # 1) 归一化窗口：保证 ROI 稳定，降低主题/布局变化导致的漂移。
            try:
                try:
                    await self.ui.set_process_frontmost("酷狗音乐")
                except Exception as e:
                    debug_info.setdefault("warnings", []).append(f"置前酷狗失败（忽略）：{e}")

                norm = await self.ui.normalize_process_window(
                    process_name="酷狗音乐",
                    width=1152,
                    height=801,
                    center_main_screen=True,
                )
                debug_info["windowNormalize"] = norm
                await asyncio.sleep(0.25)
                await _capture("kugou_fav_window_normalized")
            except Exception as e:
                _dump_json("error_window_normalize_failed", {"error": str(e), "debug": debug_info})
                raise RuntimeError(f"酷狗窗口归一化失败：{e}")

            # 2) 导航：侧边栏"我的" -> （如有必要）点击顶部"音乐"tab。
            #
            # 关键原则：先判定是否已经在"我的-音乐"内容区；若已就位则跳过"音乐"点击，避免 ROI 误采样导致硬失败。
            sidebar_roi = KUGOU_ROIS["sidebar"]

            # “我的页顶部 tabs（音乐/艺人/动态）”严格 ROI。
            # 失败证据：e5f384b2-... 显示此前用宽 ROI 会误采样到内容卡片（HOYO-MiX/全部关注），导致找不到“音乐”。
            tabs_roi = KUGOU_ROIS["my_top_tabs_strict"]

            # "我的音乐页"内容区判定 ROI：覆盖"自建歌单/默认收藏/创建歌单"等锚点。
            my_music_view_detect_roi = (0.10, 0.16, 0.90, 0.64)

            # "我喜欢入口"专用 ROI：仅覆盖左上卡片区域，避免误点右侧"已购音乐"。
            like_entry_roi = (0.06, 0.06, 0.62, 0.30)

            # 右侧半边栏 ROI
            right_panel_base = (0.55, 0.10, 0.45, 0.86)
            right_panel_list_sub = (0.05, 0.18, 0.90, 0.70)
            right_panel_list_roi = _compose_roi(right_panel_base, right_panel_list_sub)

            # 统一：ROI 转像素
            def _roi_px(roi_norm: tuple[float, float, float, float], *, iw: float, ih: float) -> dict[str, int]:
                rx, ry, rw, rh = roi_norm
                return {
                    "x": int(round(float(rx) * float(iw))) if float(iw) > 0 else 0,
                    "y": int(round(float(ry) * float(ih))) if float(ih) > 0 else 0,
                    "width": int(round(float(rw) * float(iw))) if float(iw) > 0 else 0,
                    "height": int(round(float(rh) * float(ih))) if float(ih) > 0 else 0,
                }

            # 统一：落盘 OCR boxes + ROI 裁剪图（对齐 search 的证据链风格）。
            def _dump_ocr_evidence(
                *,
                stage: str,
                screenshot_path: str,
                cap: Dict[str, Any],
                roi_norm: tuple[float, float, float, float],
                boxes: List[Any],
                ocr: Dict[str, Any],
                extra: Optional[dict[str, Any]] = None,
            ) -> Dict[str, str]:
                out: Dict[str, str] = {}
                try:
                    ts = int(time.time() * 1000)
                    out_dir = _workflow_debug_dir()

                    iw0 = float(((cap.get("imageSize") or {}).get("width")) or 0.0)
                    ih0 = float(((cap.get("imageSize") or {}).get("height")) or 0.0)
                    roi_pixels = _roi_px(roi_norm, iw=iw0, ih=ih0)

                    top = sorted(
                        boxes,
                        key=lambda b: float(getattr(b, "confidence", 0.0) or 0.0),
                        reverse=True,
                    )[:160]

                    boxes_path = out_dir / f"kugou_favorites_ocr_boxes_{stage}_{ts}.json"
                    payload: dict[str, Any] = {
                        "timestampMs": ts,
                        "stage": str(stage or ""),
                        "screenshotPath": str(screenshot_path),
                        "roiNormalized": {
                            "x": float(roi_norm[0]),
                            "y": float(roi_norm[1]),
                            "width": float(roi_norm[2]),
                            "height": float(roi_norm[3]),
                        },
                        "roiPixels": roi_pixels,
                        "captureMeta": {
                            "windowBounds": cap.get("windowBounds") or {},
                            "imageSize": cap.get("imageSize") or {},
                        },
                        "ocr": dict(ocr or {}),
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
                    if extra:
                        payload["extra"] = dict(extra)

                    boxes_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    out["ocrBoxesJson"] = str(boxes_path)

                    # ROI 裁剪图：用 sips 直接从整窗截图裁剪，保证像素不变。
                    try:
                        h = int(max(1, roi_pixels.get("height") or 1))
                        w = int(max(1, roi_pixels.get("width") or 1))
                        y = int(max(0, roi_pixels.get("y") or 0))
                        x = int(max(0, roi_pixels.get("x") or 0))
                        crop_path = out_dir / f"kugou_favorites_roi_crop_{stage}_{ts}.png"
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

                    debug_info.setdefault("evidenceFiles", []).append(out)
                    return out
                except Exception as e:
                    debug_info.setdefault("warnings", []).append(f"OCR 证据落盘失败（忽略）：{e}")
                    return out

            async def _ensure_kugou_frontmost(*, step: str) -> None:
                before = None
                after = None
                err = None

                try:
                    before = await self.ui.get_frontmost_process_name()
                except Exception as e:
                    err = str(e)

                try:
                    await self.ui.activate_app("酷狗音乐")
                except Exception:
                    pass

                try:
                    await self.ui.set_process_frontmost("酷狗音乐")
                except Exception:
                    pass

                await asyncio.sleep(0.10)

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
                ocr_box: Optional[dict[str, Any]] = None,
            ) -> dict[str, Any]:
                await _ensure_kugou_frontmost(step=f"{step}_preclick")

                if bool(debug):
                    return await self.ui.click_at_debug(
                        float(sx),
                        float(sy),
                        clicks=1,
                        tag=str(step),
                        warp_cursor=True,
                        settle_sec=0.03,
                        window_bounds=cap.get("windowBounds") or {},
                        image_size=cap.get("imageSize") or {},
                        ocr_box=ocr_box,
                    )

                await self.ui.click_at(float(sx), float(sy), clicks=1)
                return {"targetPoint": {"x": float(sx), "y": float(sy)}, "warpCursor": False}

            async def _click_box_center(cap: Dict[str, Any], box: Any, *, step: str) -> None:
                try:
                    cx, cy = box.center()
                except Exception:
                    raise RuntimeError("无法获取 OCR box 中心点")

                sx, sy = _to_screen_point(cap, image_x=float(cx), image_y=float(cy))

                ocr_box_payload = {
                    "text": str(getattr(box, "text", "") or ""),
                    "x": float(getattr(box, "x", 0.0) or 0.0),
                    "y": float(getattr(box, "y", 0.0) or 0.0),
                    "width": float(getattr(box, "width", 0.0) or 0.0),
                    "height": float(getattr(box, "height", 0.0) or 0.0),
                }

                click_debug = await _click_screen_point(cap, sx=float(sx), sy=float(sy), step=step, ocr_box=ocr_box_payload)
                debug_info.setdefault("clicks", []).append(
                    {
                        "step": step,
                        "imagePoint": {"x": float(cx), "y": float(cy)},
                        "screenPoint": {"x": float(sx), "y": float(sy)},
                        "text": ocr_box_payload["text"],
                        "ocrBox": {
                            "x": ocr_box_payload["x"],
                            "y": ocr_box_payload["y"],
                            "width": ocr_box_payload["width"],
                            "height": ocr_box_payload["height"],
                        },
                        "clickDebug": click_debug,
                    }
                )

            final_ok = False
            final_error = None
            final_song = None

            try:
                # 2.1 点击侧边栏"我的"
                await self.ui.click_text(
                    "我的",
                    tag="kugou_sidebar_my",
                    clicks=1,
                    window_owner_names=KUGOU_APP_NAMES,
                    min_confidence=0.55,
                    dry_run=dry_run,
                    roi=sidebar_roi,
                    **KUGOU_OCR_CLICK_KWARGS,
                )
                await asyncio.sleep(0.30)

                cap_after_my = await _capture("kugou_after_sidebar_my")
                after_my_path = str(cap_after_my.get("screenshotPath") or "")

                # 2.2 判定是否已经在"我的-音乐"内容区（命中任一锚点即可）
                is_my_music_view = False
                if after_my_path:
                    boxes_detect = await self.ui.ocr_screenshot_advanced(
                        after_my_path,
                        roi=my_music_view_detect_roi,
                        scale=2.4,
                        grayscale=True,
                        accurate=False,
                        custom_words=["自建歌单", "默认收藏", "创建歌单", "我喜欢", "歌单", "音乐"],
                    )
                    _dump_ocr_evidence(
                        stage="detect_my_music_view",
                        screenshot_path=after_my_path,
                        cap=cap_after_my,
                        roi_norm=my_music_view_detect_roi,
                        boxes=boxes_detect,
                        ocr={"engine": "Vision", "scale": 2.4, "grayscale": True, "accurate": False},
                    )

                    anchors = {"自建歌单", "默认收藏", "创建歌单", "歌单", "我喜欢"}
                    found = []
                    for b in boxes_detect:
                        t = str(getattr(b, "text", "") or "")
                        t_norm = _norm(t)
                        if any(_norm(a) in t_norm for a in anchors):
                            found.append(t)
                    is_my_music_view = bool(found)
                    debug_info["myMusicViewDetect"] = {
                        "ok": True,
                        "roi": my_music_view_detect_roi,
                        "anchors": sorted(list(anchors)),
                        "hits": found[:12],
                        "isMyMusicView": bool(is_my_music_view),
                    }

                # 2.3 若未就位，则点击“我的页顶部 tabs”的“音乐”（严格 ROI：必须且只能出现“音乐/艺人/动态”）。
                if not is_my_music_view:
                    cap_tabs = await _capture("kugou_my_tabs_before_click")
                    tabs_path = str(cap_tabs.get("screenshotPath") or "")
                    if not tabs_path:
                        raise RuntimeError("无法获取截图路径，无法定位顶部 tabs")

                    required_tabs_raw = ["音乐", "艺人", "动态"]
                    required_tabs = [_norm(x) for x in required_tabs_raw]
                    required_set = set(required_tabs)

                    tabs_boxes = await self.ui.ocr_screenshot_advanced(
                        tabs_path,
                        roi=tabs_roi,
                        scale=3.2,
                        grayscale=True,
                        accurate=False,
                        custom_words=required_tabs_raw,
                    )
                    _dump_ocr_evidence(
                        stage="click_my_music_tab",
                        screenshot_path=tabs_path,
                        cap=cap_tabs,
                        roi_norm=tabs_roi,
                        boxes=tabs_boxes,
                        ocr={"engine": "Vision", "scale": 3.2, "grayscale": True, "accurate": False},
                    )

                    # 严格校验：仅允许出现指定 tabs 文本（按 min_confidence 过滤）。
                    min_confidence = 0.6
                    token_set: set[str] = set()
                    by_text: dict[str, list[Any]] = {}
                    for b in tabs_boxes:
                        try:
                            conf = float(getattr(b, "confidence", 0.0) or 0.0)
                        except Exception:
                            conf = 0.0
                        if conf < float(min_confidence):
                            continue

                        t = _norm(str(getattr(b, "text", "") or ""))
                        if not t:
                            continue

                        token_set.add(t)
                        if t in required_set:
                            by_text.setdefault(t, []).append(b)

                    missing = [t for t in required_tabs if t not in token_set]
                    extra = [t for t in sorted(token_set) if t not in required_set]

                    debug_info.setdefault("myTopTabsStrict", []).append(
                        {
                            "roi": tabs_roi,
                            "minConfidence": float(min_confidence),
                            "tokens": sorted(token_set),
                            "missing": missing,
                            "extra": extra,
                        }
                    )

                    if missing or extra or len(token_set) != len(required_set):
                        preview = [str(getattr(b, "text", "") or "") for b in tabs_boxes[:20]]
                        raise RuntimeError(
                            "未能通过严格 tabs ROI 校验（"
                            f"required={required_tabs_raw}, missing={missing}, extra={extra}, tokens={sorted(token_set)}, preview={preview}）"
                        )

                    music_key = _norm("音乐")
                    music_boxes = by_text.get(music_key) or []
                    if not music_boxes:
                        preview = [str(getattr(b, "text", "") or "") for b in tabs_boxes[:20]]
                        raise RuntimeError(f"严格 tabs ROI 已命中但未找到‘音乐’框（preview={preview}）")

                    def _pick_box(b: Any) -> tuple[float, float, float]:
                        try:
                            yv = float(getattr(b, "y", 0.0) or 0.0)
                        except Exception:
                            yv = 10**9
                        try:
                            xv = float(getattr(b, "x", 0.0) or 0.0)
                        except Exception:
                            xv = 10**9
                        try:
                            conf = float(getattr(b, "confidence", 0.0) or 0.0)
                        except Exception:
                            conf = 0.0
                        return (yv, xv, -conf)

                    tab_box = min(music_boxes, key=_pick_box)
                    await _click_box_center(cap_tabs, tab_box, step="click_my_music_tab")
                    await asyncio.sleep(0.35)

                if dry_run:
                    final_ok = True
                    return {"message": f"(dry-run) 将在酷狗进入我喜欢并播放（pickMode={pm}）", "debug": debug_info if debug else None}

                # 3) 查找并点击"我喜欢"入口（专用 ROI + 消歧 + 可复盘证据）
                like_target = "我喜欢"
                like_box = None
                like_cap: Optional[Dict[str, Any]] = None

                for attempt in range(10):
                    cap = await _capture(f"kugou_my_like_entry_probe_{attempt}")
                    path = str(cap.get("screenshotPath") or "")
                    if path:
                        boxes = await self.ui.ocr_screenshot_advanced(
                            path,
                            roi=like_entry_roi,
                            scale=3.2,
                            grayscale=True,
                            accurate=False,
                            custom_words=["我喜欢", "喜欢", "已购音乐", "本地", "最近播放"],
                        )

                        # 仅在 debug 或第0次/最终失败/命中时落盘证据，避免产物爆炸。
                        if bool(debug) or attempt == 0:
                            _dump_ocr_evidence(
                                stage=f"find_like_entry_{attempt}",
                                screenshot_path=path,
                                cap=cap,
                                roi_norm=like_entry_roi,
                                boxes=boxes,
                                ocr={"engine": "Vision", "scale": 3.2, "grayscale": True, "accurate": False},
                                extra={"attempt": int(attempt)},
                            )

                        target_norm = _norm(like_target)
                        roi_pixels = _roi_px(like_entry_roi, iw=float(((cap.get("imageSize") or {}).get("width")) or 0.0), ih=float(((cap.get("imageSize") or {}).get("height")) or 0.0))
                        roi_w = float(roi_pixels.get("width") or 0)

                        candidates = []
                        for b in boxes:
                            t = str(getattr(b, "text", "") or "")
                            tn = _norm(t)
                            if not tn:
                                continue
                            if not (tn == target_norm or target_norm in tn):
                                continue

                            # 过滤异常宽框：Vision 有时会把一整排卡片合成一行，中心点会落到"已购音乐"。
                            try:
                                bw = float(getattr(b, "width", 0.0) or 0.0)
                            except Exception:
                                bw = 0.0
                            if roi_w > 1 and bw >= roi_w * 0.85:
                                continue

                            candidates.append(b)

                        if candidates:
                            like_box = min(candidates, key=lambda b: (float(getattr(b, "y", 0.0) or 0.0), float(getattr(b, "x", 0.0) or 0.0)))
                            like_cap = cap
                            break

                    # 没找到则向上滚动，继续探测。
                    try:
                        await self.ui.scroll_wheel(delta_y=12, steps=2, unit="line")
                    except Exception as e:
                        debug_info.setdefault("warnings", []).append(f"滚动失败（忽略）：{e}")
                    await asyncio.sleep(0.10)

                if like_box is None or like_cap is None:
                    _dump_json(
                        "error_like_entry_not_found",
                        {
                            "error": "未能在我的音乐页识别到'我喜欢'入口",
                            "roi": like_entry_roi,
                            "debug": debug_info,
                        },
                    )
                    raise RuntimeError("未能在我的音乐页识别到'我喜欢'入口")

                await _click_box_center(like_cap, like_box, step="click_my_like")
                await asyncio.sleep(0.45)

                # 4) 右侧半边栏：先做"打开成功"宽松校验（≥3 关键词命中），再在列表 ROI 内抽歌。
                cap_panel = await _capture("kugou_my_like_panel")
                panel_path = str(cap_panel.get("screenshotPath") or "")
                if not panel_path:
                    raise RuntimeError("无法获取截图路径，无法在右侧面板中识别歌曲")

                panel_check_boxes = await self.ui.ocr_screenshot_advanced(
                    panel_path,
                    roi=right_panel_base,
                    scale=2.4,
                    grayscale=True,
                    accurate=False,
                    custom_words=["单曲", "歌手", "视频", "猜你喜欢", "顺序播放", "随机播放", "单曲播放"],
                )
                _dump_ocr_evidence(
                    stage="panel_open_check",
                    screenshot_path=panel_path,
                    cap=cap_panel,
                    roi_norm=right_panel_base,
                    boxes=panel_check_boxes,
                    ocr={"engine": "Vision", "scale": 2.4, "grayscale": True, "accurate": False},
                )

                required = ["单曲", "歌手", "视频", "猜你喜欢", "顺序播放", "随机播放", "单曲播放"]
                hit = set()
                for b in panel_check_boxes:
                    t = _norm(str(getattr(b, "text", "") or ""))
                    for kw in required:
                        if _norm(kw) in t:
                            hit.add(kw)

                debug_info["rightPanelOpenCheck"] = {
                    "required": required,
                    "hit": sorted(list(hit)),
                    "hitCount": int(len(hit)),
                }

                if len(hit) < 3:
                    payload = {
                        "error": "右侧半边栏打开校验未通过",
                        "required": required,
                        "hit": sorted(list(hit)),
                        "hitCount": int(len(hit)),
                        "roi": {"panel": right_panel_base},
                        "preview": [str(getattr(b, "text", "") or "") for b in panel_check_boxes[:20]],
                        "debug": debug_info,
                    }
                    _dump_json("error_panel_open_check_failed", payload)
                    raise RuntimeError(f"右侧半边栏打开校验未通过（hitCount={len(hit)}）")

                # 4.2 列表 ROI 抽歌名
                panel_boxes = await self.ui.ocr_screenshot_advanced(
                    panel_path,
                    roi=right_panel_list_roi,
                    scale=2.4,
                    grayscale=True,
                    accurate=False,
                    custom_words=["单曲", "歌手", "视频", "猜你喜欢", "顺序播放", "随机播放", "单曲播放", "VIP", "MV"],
                )
                _dump_ocr_evidence(
                    stage="panel_song_list",
                    screenshot_path=panel_path,
                    cap=cap_panel,
                    roi_norm=right_panel_list_roi,
                    boxes=panel_boxes,
                    ocr={"engine": "Vision", "scale": 2.4, "grayscale": True, "accurate": False},
                )

                ignore = {
                    "单曲",
                    "歌手",
                    "视频",
                    "猜你喜欢",
                    "顺序播放",
                    "随机播放",
                    "单曲播放",
                    "我喜欢",
                    "喜欢",
                    "播放",
                    "暂停",
                    "VIP",
                    "MV",
                }

                candidates: list[Any] = []
                for b in panel_boxes:
                    text = str(getattr(b, "text", "") or "").strip()
                    if not text:
                        continue

                    t_norm = _norm(text)
                    if not t_norm:
                        continue

                    if t_norm in {_norm(x) for x in ignore}:
                        continue

                    # 过滤明显非歌曲项。
                    if "：" in text or ":" in text:
                        continue
                    if len(text) < 2 or len(text) > 40:
                        continue

                    candidates.append(b)

                if not candidates:
                    payload = {
                        "error": "未能在右侧面板中识别到可播放歌曲名",
                        "roi": {"base": right_panel_base, "list": right_panel_list_roi},
                        "preview": [str(getattr(b, "text", "") or "") for b in panel_boxes[:20]],
                        "debug": debug_info,
                    }
                    _dump_json("error_no_song_candidates", payload)
                    raise RuntimeError(f"未能在右侧面板中识别到可播放歌曲名（preview={payload['preview']}）")

                chosen = random.choice(candidates) if pm == "random" else min(candidates, key=_pos_key)
                final_song = str(getattr(chosen, "text", "") or "")

                await _click_box_center(cap_panel, chosen, step="play_like_song")
                await asyncio.sleep(0.25)

                final_ok = True
                return {"message": f"已在酷狗从我喜欢列表播放：{final_song}", "debug": debug_info if debug else None}
            except Exception as e:
                final_error = str(e)
                raise
            finally:
                # 无论成功/失败都落盘 summary，避免再出现"只有 PNG 没 JSON 无法复盘"。
                _dump_json(
                    "summary",
                    {
                        "ok": bool(final_ok),
                        "error": final_error,
                        "song": final_song,
                        "debug": debug_info,
                    },
                )

        if action == "playlist":
            if not query:
                raise RuntimeError("playlist 需要提供 query")

            # 注意：酷狗的 Cmd+F 不会聚焦搜索框（已验证）。这里优先走菜单/相对点击，不依赖 OCR。
            try:
                debug_info["frontmostProcess"] = await self.ui.get_frontmost_process_name()
            except Exception:
                pass

            before_capture = None
            if not dry_run:
                before_capture = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_playlist_before")
                debug_info["beforeCapture"] = before_capture

            # 先尝试从菜单进入搜索，再在顶部搜索框附近点几下，让输入框拿到焦点。
            # 这里不要只看 frontmost（Electron 可能抢焦点）；要明确把操作打到酷狗进程上。
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

            # 先清空输入框，再输入 query。
            try:
                await self.ui.hotkey("a", modifiers=["command down"])
                await self.ui.key_code(51)
            except Exception as e:
                debug_info.setdefault("warnings", []).append(f"清空输入框失败（忽略）：{e}")

            await self.ui.type_text(query)
            await self.ui.key_code(36)
            await asyncio.sleep(0.7)

            # 尝试切到"歌单"tab（可能因为自绘 UI / OCR 不稳而失败；失败也继续往下走）。
            await self._best_effort_click_any(
                ["歌单", "歌 单"],
                tag="kugou_tab_playlist",
                dry_run=dry_run,
                debug=debug_info,
                click_text_kwargs={"roi": KUGOU_ROIS["full"], **KUGOU_OCR_CLICK_KWARGS},
                allow_fail=True,
            )
            await asyncio.sleep(0.4)

            # 用键盘选中第一条结果并回车播放。
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

            mode = str(UI_AUTOMATION_CONFIG.mode or "ocr").strip().lower()
            if mode == "vlm":
                # 完全 VLM 模式：由本地 Ollama VLM 决策 click/type_text/noop 并驱动执行。
                # 注意：该模式不回退 OCR（满足“两个完全不同模式”的约束）。
                driver = VlmUiDriver(ui=self.ui)
                return await driver.run_kugou_search_play(query=query, debug=debug, dry_run=dry_run)

            # 默认：纯 OCR 工作流（导航环节不使用坐标/键盘兜底）。
            return await self._kugou_search_ocr_workflow(query=query, debug=debug, dry_run=dry_run)

            ocr_min_confidence = 0.75

            # ROIs（归一化，左上为原点）
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

                # 注意：
                # - 酷狗的 Cmd+F 不会聚焦搜索框（已由用户验证）
                # - 用 System Events 输入中文可能会弹输入法选择框
                # - 为了更稳：先回到首页，再按窗口边界相对坐标点击并用粘贴输入 query
                try:
                    debug_info["frontmostProcess"] = await self.ui.get_frontmost_process_name()
                except Exception:
                    pass

                # 这里不要只看 frontmost（Electron 可能抢焦点）；要明确把操作打到酷狗进程上。
                try:
                    await self.ui.set_process_frontmost("酷狗音乐")
                except Exception as e:
                    debug_info.setdefault("warnings", []).append(f"置前酷狗失败（忽略）：{e}")

                # 先回到首页（用户要求）。
                # 酷狗可能一开始在歌曲详情页，所以多试几种"返回/回首页"的快捷键。
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

                # 基于窗口边界的相对点击。
                # 重要：在部分 macOS 环境里，`kCGWindowBounds.Y` 的口径更像"从上往下"。
                # 从我们调试结果看，酷狗搜索入口用：y = bounds.y + bounds.height * y_ratio_from_top 更稳定。
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

                    # 只有搜索结果加载后才会出现的 tabs。
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
                        # 保存一个紧凑的底部区域 hash，作为检测进度变化的弱兜底。
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

                    # 弱兜底：底部 hash 会随播放进度变化。
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

                # 重要：想进入搜索，酷狗最好处在"音乐"页（不要在"我的"页）。
                # "我的"页侧边栏字比较小，OCR 不太稳，所以这里用几个更稳定的侧边栏点位。
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

                # 注意：这里不要用底栏来做"是否正在播放"的确认。
                # 不同主题下底栏可能不显示，且进度条的 OCR 容易飘。

                # 重要：这里不点菜单项，因为菜单匹配可能命中到不相关项（比如"最近使用"），容易把酷狗带跑。
                debug_info.setdefault("menu", []).append({"skipped": True, "reason": "避免副作用"})

                # 进入搜索，并确保已经到了搜索结果页。
                # 不同版本酷狗首页的搜索框位置会有点差异，所以这里多试几个点位。
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

                # 用感知哈希判断是否从首页切到了搜索页。
                # 注意：直接用 sha256 太敏感（光标闪烁/hover 都会变），容易误判。
                nav_path = str(((nav_capture or {}).get("screenshotPath")) or "")
                nav_phash = _phash_ahash(nav_path) if nav_path else ""
                if nav_phash:
                    debug_info["navPHash"] = nav_phash

                # 如果已经在搜索页（比如上次执行结束停在结果页），那 baseline 可能本来就是搜索页。
                # 这里先点一次右上角"取消"退出搜索，再重新截一张作为 baseline，这样后面的判断更有意义（不靠 OCR）。
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

                    # 先清空输入框再粘贴搜索词（避免输入法弹窗干扰）。
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

                    # 优先选下拉联想的第一条，然后回车执行搜索。
                    await self.ui.key_code(125)  # 下箭头
                    await asyncio.sleep(0.12)
                    await self.ui.key_code(36)  # 回车
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

                    # 优先用 OCR 判定结果页：必须能看到"取消"且 >=50% 的结果 tabs。
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

                    # 二次确认，避免误报。
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

                # 尝试切到"单曲/歌曲"tab（可能因为 OCR 不稳而失败；失败也继续往下走）。
                await self._best_effort_click_any(
                    ["单曲", "歌曲", "综合"],
                    tag="kugou_tab_song",
                    dry_run=dry_run,
                    debug=debug_info,
                    click_text_kwargs={"roi": KUGOU_ROIS["full"], **KUGOU_OCR_CLICK_KWARGS},
                    allow_fail=True,
                )
                await asyncio.sleep(0.35)

                # 5) 播放第一条搜索结果。
                # 优先用 OCR + 几何偏移：先锚定 tab 行（综合/单曲/歌曲），再点 tab 下方的第一行。
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
                        # 2) 兜底：锚定 tab 行（综合/单曲/歌曲），点 tab 下方第一行。
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

                # 注意：不通过底部播放栏验证播放状态。

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

                # 尝试退出详情页（避免按到会切换 tab/页面的键）。
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

                # 注意：用这个截图来算窗口边界。pHash 基线应该在确认进入搜索页后再取，
                # 否则会导致误报。
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
                    """判断当前界面是否已经在搜索结果页。

                    用户确认的规则：
                    - 必须能在顶部搜索栏看到"取消"
                    - 必须能看到 >=50% 的结果 tabs（综合/单曲/视频/歌单/听书/专辑/歌词）
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

                        # 弱兜底：底部区域 hash 会随播放进度变化。
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

                    # 音乐主页必须同时满足两个条件：
                    # - 能看到 >=3/4 的顶部 tabs（推荐/频道/歌单/歌手）
                    # - 能在 top_search ROI 内看到"搜索"入口（否则可能被弹窗遮挡）
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
                    """确保当前处于搜索页。

                    酷狗 UI 有很多子页面/弹窗。我们先收敛到能看到搜索栏的状态，
                    然后进入搜索页（会显示"取消"/"历史搜索"）。
                    """

                    # 稳定的返回按钮点击点位。
                    # 避免点到音乐主页上的头像区域。
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

                        # 如果不在搜索页，先尝试返回到音乐主页。
                        # 这可以修复一开始误点进入"我的/个人主页"的情况。
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
                                debug_info.setdefault("warnings", []).append(f"OCR 点击'音乐'失败（round={round_idx}），改用坐标兜底：{e}")
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
                            # 点击 top_search ROI 内的搜索占位符。
                            try:
                                pt = state.get("searchPoint")
                                if isinstance(pt, dict) and pt.get("x") and pt.get("y"):
                                    await self.ui.click_at(float(pt["x"]), float(pt["y"]), clicks=1)
                                    debug_info.setdefault("clicks", []).append({"step": "enter_search", "via": "searchPoint", "point": pt})
                                else:
                                    # 坐标优先：在某些主题下比 OCR 更稳定。
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

                        # needs_back: 优先反复点击返回按钮。
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

                        # 兜底 1：点左侧内容区域的灰色空白区。
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

                        # 兜底 2：键盘返回。
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
                    debug_info.setdefault("warnings", []).append(f"OCR 点击'音乐'失败，改用坐标兜底：{e}")
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

                # 搜索页进入后取 pHash 基线（在输入搜索词之前）。
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

                # 3) 聚焦输入框然后粘贴搜索词。
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

                    # 只有当焦点看起来像文本输入框时才继续。
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

                    # 优先选下拉联想的第一条，然后回车执行搜索。
                    await self.ui.key_code(125)  # 下箭头
                    await asyncio.sleep(0.12)
                    await self.ui.key_code(36)  # 回车
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

                    # 优先用 OCR 判定结果页：必须能看到"取消"且 >=50% 的 tabs。
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

                    # 如果 OCR 不稳，就用 pHash 兜底。
                    if base_phash and cap_phash and dist >= phash_threshold:
                        found_results = True
                        break

                if not found_results:
                    raise RuntimeError(f"OCR-first 未能可靠进入搜索结果页。最后截图：{last_attempt_path}")

                # 尝试切到"单曲"tab（可能因为 OCR 不稳而失败；失败也继续）。
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

                # 播放第一条搜索结果。
                # 用 OCR + 几何偏移定位 tab 行下方的第一行。
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
                        # 2) 兜底：锚定 tab 行（综合/单曲/歌曲），点 tab 下方第一行。
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
                    # 兜底：点结果列表靠上的位置，减少选到第 5+ 行的概率。
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

                # 注意：不通过底部播放栏验证播放状态。

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

                # 优先用 pHash 验证，避免因界面微小变化导致的误报。
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
        """打开并置前酷狗。

        重要：macOS 上 UI 点击事件总是发送给当前前台应用。
        如果酷狗不在前台（比如在另一个桌面空间），点击可能会打到别的窗口。
        """

        # 尝试常见的应用名。
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
        """酷狗搜索播放工作流（纯 OCR 导航版）。

        约束条件（用户确认）：
        - 点击侧边栏"音乐"之前：先将酷狗窗口归一化到 1152x801 并居中于主屏幕。
        - 总是先点侧边栏"音乐"（精准 OCR 点击）进入音乐页。
        - 音乐主页判定条件：tabs 命中数 >=3/4 且能在 top_search ROI 内看到"搜索"。
        - 如果能看到 tabs 但看不到"搜索"，视为有遮挡弹窗（小窗/右侧窗或大窗），
          需要点击该弹窗左上角的返回按钮（通过 OCR 锚定）来关闭。
        - 进入搜索页 -> 提交查询 -> 在结果页点"单曲" -> 点击目标歌名（单击）。
        - 点击歌名后，通过底部播放栏 OCR 和进度/hash 变化来验证播放。
        - 导航环节不使用坐标/键盘兜底（搜索词的输入/提交仍用键盘）。
        """

        debug_info: Dict[str, Any] = {"mode": "kugou_ocr_workflow_v2"}

        # 分开两个阈值：状态检测可以宽松些，实际点击时要求高一些。
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
                # 用明确的进程名；OCR/点击流程假设酷狗是前台应用。
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
            # 安全护栏：确保酷狗是当前前台应用，否则点击会打到别的窗口。
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
            """落盘当前 debug_info，用于事后排查。"""

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

        # 在执行任何点击之前先截一张初始状态图，便于追溯。
        init_cap: Optional[Dict[str, Any]] = None
        try:
            init_cap = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_flow_init")
            debug_info.setdefault("captures", []).append({"step": "kugou_flow_init", "capture": init_cap})
        except Exception as e:
            debug_info.setdefault("warnings", []).append(f"初始截图失败（忽略）：{e}")
        _dump_debug_info("init")

        # 尽早归一化窗口尺寸/位置，减少 ROI/OCR 识别的不稳定性。
        # 性能优化：如果当前窗口已经满足目标尺寸/位置（允许少量像素误差），则跳过归一化。
        cap_norm: Optional[Dict[str, Any]] = None

        def _is_already_normalized(cap: Dict[str, Any]) -> tuple[bool, Dict[str, Any]]:
            wb = cap.get("windowBounds") or {}
            if not isinstance(wb, dict):
                return (False, {"ok": False, "reason": "missing_windowBounds"})

            try:
                cur_w = int(round(float(wb.get("width") or 0.0)))
                cur_h = int(round(float(wb.get("height") or 0.0)))
                cur_x = int(round(float(wb.get("x") or 0.0)))
                cur_y = int(round(float(wb.get("y") or 0.0)))
            except Exception:
                return (False, {"ok": False, "reason": "invalid_windowBounds"})

            target_w = 1152
            target_h = 801

            # 复用 `normalize_process_window()` 的目标位置计算：主屏居中。
            try:
                screen = self.ui._get_main_screen_size()  # noqa: SLF001
                target_x = int(max(0, round((float(screen.width) - float(target_w)) / 2.0)))
                target_y = int(max(0, round((float(screen.height) - float(target_h)) / 2.0)))
            except Exception as e:
                return (False, {"ok": False, "reason": "screen_size_unavailable", "error": str(e)})

            tol = 2
            size_ok = bool(abs(cur_w - target_w) <= tol and abs(cur_h - target_h) <= tol)
            pos_ok = bool(abs(cur_x - target_x) <= tol and abs(cur_y - target_y) <= tol)
            ok = bool(size_ok and pos_ok)
            return (
                ok,
                {
                    "ok": bool(ok),
                    "reason": "already_normalized" if ok else "mismatch",
                    "tolerancePx": int(tol),
                    "current": {"x": cur_x, "y": cur_y, "width": cur_w, "height": cur_h},
                    "target": {"x": target_x, "y": target_y, "width": int(target_w), "height": int(target_h)},
                    "screen": {"width": int(screen.width), "height": int(screen.height)},
                },
            )

        try:
            await _ensure_kugou_frontmost(step="kugou_window_normalize")

            should_skip = False
            skip_meta: Dict[str, Any] = {"ok": False, "reason": "no_init_cap"}
            if init_cap is not None:
                should_skip, skip_meta = _is_already_normalized(init_cap)

            if should_skip:
                debug_info["windowNormalize"] = {"ok": True, "skipped": True, **skip_meta}
                cap_norm = init_cap
                debug_info.setdefault("captures", []).append(
                    {
                        "step": "kugou_window_normalized",
                        "capture": cap_norm,
                    }
                )
            else:
                norm = await self.ui.normalize_process_window(
                    process_name="酷狗音乐",
                    width=1152,
                    height=801,
                    center_main_screen=True,
                )
                debug_info["windowNormalize"] = {"ok": True, "skipped": False, **(norm or {})}
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
                # 状态判定对顶部小 tabs/搜索文本最敏感。
                # 优先在收紧的 ROI 上做高 scale OCR，避免漏掉主 tabs。
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

            # 酷狗 UI 因版本/主题而异。
            # 目前音乐主页比较稳定的识别指标是中间 tabs 行。
            main_tabs = ["歌单", "音频", "歌手", "专辑", "视频"]
            main_hits = 0
            for t in main_tabs:
                if _find_best_box(boxes, target=t, roi_px=roi_tabs, min_conf=detect_min_confidence):
                    main_hits += 1

            # 搜索结果页通常会显示一行 tabs，包含"综合"/"单曲"。
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

            # OCR 可能识别不到低对比度的 placeholder "搜索"。
            # 这里对"音乐主页"的判定比较宽松：tabs hits >= 2/4 即可，不强制要求"搜索"。
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
            """把窗口截图内的坐标（左上为原点）转换成屏幕坐标。"""

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
            """通过页面标题锚点定位顶部左侧的返回按钮并点击。

            酷狗很多返回按钮是图标（OCR 识别不了），但"分类"这类页面标题是能识别的，
            我们就在标题左侧点击，作为一种确定性的 OCR 锚定操作。
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
                (0.42, 0.0, 0.58, 0.18),  # 右侧小弹窗顶部
                (0.18, 0.0, 0.82, 0.18),  # 大内容弹窗顶部
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

        async def _click_sidebar_music(
            *,
            tag: str,
            step_name: str,
            cap_hint: Optional[Dict[str, Any]] = None,
            cap_hint_max_age_ms: int = 2000,
        ) -> None:
            """点击侧边栏“音乐”。

            性能优化：允许复用上一张“足够新”的窗口截图作为 OCR 输入，避免重复 `screencapture`。

            - `cap_hint`：候选复用截图（例如刚刚的 `kugou_window_normalized`）。
            - `cap_hint_max_age_ms`：候选截图与当前时间的最大允许间隔。
            """

            def _extract_ts_ms_from_path(path: str) -> Optional[int]:
                m = re.search(r"_(\d{10,})\.png$", str(path or ""))
                if not m:
                    return None
                try:
                    return int(m.group(1))
                except Exception:
                    return None

            cap0: Dict[str, Any]
            reused = False
            if cap_hint is not None:
                path_hint = str(cap_hint.get("screenshotPath") or "")
                ts_hint = _extract_ts_ms_from_path(path_hint)
                now_ms = int(time.time() * 1000)
                if path_hint and ts_hint is not None and (0 <= now_ms - int(ts_hint) <= int(cap_hint_max_age_ms)):
                    cap0 = cap_hint
                    reused = True
                else:
                    cap0 = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=tag)
            else:
                cap0 = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=tag)

            debug_info.setdefault("captures", []).append({"step": tag, "capture": cap0, "reused": bool(reused)})

            path0 = str(cap0.get("screenshotPath") or "")
            if not path0:
                raise RuntimeError("无法获取酷狗窗口截图路径")

            boxes_music = await self.ui.ocr_screenshot_advanced(
                path0,
                # 用收紧的 ROI 只识别顶部的"音乐"入口，减少误识别。
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
                # 优先取收紧 ROI 内最靠上的"音乐"标签。
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
                # 用稍宽一点的侧边栏 ROI，确保能覆盖三个主入口。
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
                # 优先取最靠上的"音乐"标签。
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

            # 如果 OCR 没识别到"音乐"，就锚定"视频"/"我的"，往上偏移点击预期位置。
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

        # 1) 先点侧边栏"音乐"。
        # 性能优化：优先复用 `cap_norm`（刚归一化后的截图）做 OCR，避免重复 `screencapture`。
        try:
            await _click_sidebar_music(
                tag="kugou_sidebar_music",
                step_name="kugou_sidebar_music",
                cap_hint=cap_norm,
                cap_hint_max_age_ms=2500,
            )
            await asyncio.sleep(0.25)
        except Exception as e:
            debug_info.setdefault("warnings", []).append(f"侧边栏点击'音乐'失败（继续）：{e}")
        _dump_debug_info("after_sidebar_music")

        # 2) 收敛到可以输入搜索词的状态（搜索页或结果页）。
        cap: Dict[str, Any]
        boxes: List[Any]
        iw = 0.0
        ih = 0.0
        enter_search_fallback_tries = 0
        did_force_recommend = False

        async def _force_recommend_tab(*, cap_in: Dict[str, Any], step_idx: int) -> None:
            """尽力点击两次“推荐”，确保回到推荐子页并退出可能存在的半栏面板。

            该步骤属于“稳定性护栏”，不应该因为 OCR 严格校验失败而中断整条 `search` 流程：
            - 成功：双击“推荐”，并尽力复核 tabs 是否仍在预期 ROI 内；
            - 失败：写入 debug_info 的 warnings 与 recommendTabsStrict，继续后续流程。
            """

            required_raw = ["推荐", "频道", "歌单", "歌手"]
            required = [_norm(x) for x in required_raw]
            required_set = set(required)

            # 说明：推荐 tabs 文本在部分主题下对比度较低，min_confidence 过高会导致“全缺失”。
            min_confidence = 0.45

            screenshot_path = str(cap_in.get("screenshotPath") or "")
            if not screenshot_path:
                debug_info.setdefault("warnings", []).append("无法获取截图路径，跳过强制回到推荐子页")
                return

            roi_candidates = [
                KUGOU_ROIS["music_recommend_tabs_strict"],
                KUGOU_ROIS.get("music_recommend_tabs_strict_legacy", (0.18, 0.12, 0.56, 0.08)),
            ]

            # 去重（避免 env 覆盖与 legacy 一样导致重复 OCR）。
            rois_to_try: list[tuple[float, float, float, float]] = []
            seen: set[tuple[float, float, float, float]] = set()
            for r in roi_candidates:
                key = (round(r[0], 4), round(r[1], 4), round(r[2], 4), round(r[3], 4))
                if key in seen:
                    continue
                seen.add(key)
                rois_to_try.append(r)

            def _pick_box(b: Any) -> tuple[float, float, float]:
                try:
                    yv = float(getattr(b, "y", 0.0) or 0.0)
                except Exception:
                    yv = 10**9
                try:
                    xv = float(getattr(b, "x", 0.0) or 0.0)
                except Exception:
                    xv = 10**9
                try:
                    conf = float(getattr(b, "confidence", 0.0) or 0.0)
                except Exception:
                    conf = 0.0
                return (yv, xv, -conf)

            chosen_roi: Optional[tuple[float, float, float, float]] = None
            chosen_rec_box: Optional[Any] = None

            for roi_idx, roi in enumerate(rois_to_try):
                boxes_rec = await self.ui.ocr_screenshot_advanced(
                    screenshot_path,
                    roi=roi,
                    scale=3.2,
                    grayscale=True,
                    accurate=False,
                    custom_words=required_raw,
                )

                token_set: set[str] = set()
                by_text: dict[str, list[Any]] = {}
                for b in boxes_rec:
                    try:
                        conf = float(getattr(b, "confidence", 0.0) or 0.0)
                    except Exception:
                        conf = 0.0
                    if conf < float(min_confidence):
                        continue

                    t = _norm(str(getattr(b, "text", "") or ""))
                    if not t:
                        continue

                    token_set.add(t)
                    if t in required_set:
                        by_text.setdefault(t, []).append(b)

                missing = [t for t in required if t not in token_set]
                extra = [t for t in sorted(token_set) if t not in required_set]

                debug_info.setdefault("recommendTabsStrict", []).append(
                    {
                        "phase": "beforeClick",
                        "step": int(step_idx),
                        "roiIndex": int(roi_idx),
                        "roi": roi,
                        "minConfidence": float(min_confidence),
                        "tokens": sorted(token_set),
                        "missing": missing,
                        "extra": extra,
                    }
                )

                if missing or extra or len(token_set) != len(required_set):
                    continue

                rec_key = _norm("推荐")
                rec_boxes = by_text.get(rec_key) or []
                if not rec_boxes:
                    debug_info.setdefault("warnings", []).append(
                        f"严格推荐 tabs ROI 已命中但未找到‘推荐’框（step={step_idx}, roiIndex={roi_idx}）"
                    )
                    continue

                chosen_roi = roi
                chosen_rec_box = min(rec_boxes, key=_pick_box)
                break

            if not chosen_roi or chosen_rec_box is None:
                debug_info.setdefault("warnings", []).append(
                    f"严格推荐 tabs ROI 校验失败，跳过强制回到推荐子页（step={step_idx}）"
                )
                return

            # 按需求：强制点击两次“推荐”。
            await _click_box_center(cap_in, chosen_rec_box, step=f"force_recommend_click_1_{step_idx}")
            await asyncio.sleep(0.15)
            await _click_box_center(cap_in, chosen_rec_box, step=f"force_recommend_click_2_{step_idx}")
            await asyncio.sleep(0.35)

            # 复核：点击后仍应保持严格 tabs ROI（尽力确认处于推荐子页的稳定态；失败不阻断流程）。
            cap2 = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=f"kugou_recommend_verify_{step_idx}")
            debug_info.setdefault("captures", []).append({"step": f"kugou_recommend_verify_{step_idx}", "capture": cap2})

            path2 = str(cap2.get("screenshotPath") or "")
            if not path2:
                debug_info.setdefault("warnings", []).append("无法获取复核截图路径，跳过推荐 tabs 复核")
                return

            boxes2 = await self.ui.ocr_screenshot_advanced(
                path2,
                roi=chosen_roi,
                scale=3.2,
                grayscale=True,
                accurate=False,
                custom_words=required_raw,
            )

            token_set2: set[str] = set()
            for b in boxes2:
                try:
                    conf = float(getattr(b, "confidence", 0.0) or 0.0)
                except Exception:
                    conf = 0.0
                if conf < float(min_confidence):
                    continue

                t = _norm(str(getattr(b, "text", "") or ""))
                if t:
                    token_set2.add(t)

            missing2 = [t for t in required if t not in token_set2]
            extra2 = [t for t in sorted(token_set2) if t not in required_set]

            debug_info.setdefault("recommendTabsStrict", []).append(
                {
                    "phase": "afterClick",
                    "step": int(step_idx),
                    "roiIndex": int(rois_to_try.index(chosen_roi)),
                    "roi": chosen_roi,
                    "minConfidence": float(min_confidence),
                    "tokens": sorted(token_set2),
                    "missing": missing2,
                    "extra": extra2,
                }
            )

            if missing2 or extra2 or len(token_set2) != len(required_set):
                debug_info.setdefault("warnings", []).append(
                    "推荐 tabs 复核失败（"
                    f"required={required_raw}, missing={missing2}, extra={extra2}, tokens={sorted(token_set2)}）"
                )

        for step in range(6):
            cap, boxes, iw, ih = await _capture_full(f"kugou_flow_state_{step}")
            state = _detect_state(boxes, iw=iw, ih=ih)

            # 方案2：补充“焦点信号”兜底。
            # 说明：有些 UI 状态下 OCR 可能漏掉“取消/历史搜索”，但实际上已经在搜索输入态。
            # 当顶部 ROI 内出现“搜索/搜/索”任一字样且焦点为文本输入控件时，直接视为 search_view。
            if state.get("mode") == "music_main_ready":
                focus_info: Optional[Dict[str, Any]] = None
                try:
                    focus_info = await self.ui.get_focused_ui_element_info("酷狗音乐")
                except Exception:
                    focus_info = None

                role = str((focus_info or {}).get("role") or "")
                focus_is_text = bool((focus_info or {}).get("ok") is True and ("Text" in role or "Field" in role))

                roi_top = _roi_px(KUGOU_ROIS["top_search_verify"], iw=iw, ih=ih)
                has_search_like = bool(
                    _find_best_box(boxes, target="搜索", roi_px=roi_top, min_conf=detect_min_confidence)
                    or _find_best_box(boxes, target="搜", roi_px=roi_top, min_conf=detect_min_confidence)
                    or _find_best_box(boxes, target="索", roi_px=roi_top, min_conf=detect_min_confidence)
                )

                if focus_is_text and has_search_like:
                    prev = dict(state)
                    state["mode"] = "search_view"
                    state["searchViewByFocus"] = True
                    debug_info.setdefault("stateOverrides", []).append(
                        {"step": int(step), "from": prev, "to": dict(state), "focused": focus_info}
                    )

            debug_info.setdefault("flow", []).append({"step": step, "state": state})
            _dump_debug_info(f"state_{step}")

            if state.get("mode") in {"search_view", "results_page"}:
                break

            if state.get("mode") == "music_main_ready":
                # 按需求：若尚未进入搜索子界面，则先强制回到“推荐”子页（双击“推荐”）。
                if not did_force_recommend:
                    await _force_recommend_tab(cap_in=cap, step_idx=int(step))
                    did_force_recommend = True
                    await asyncio.sleep(0.25)
                    continue

                # 进入搜索页。
                # - 优先在 search_bar ROI 内做 OCR（对低对比度的 placeholder "搜索"效果更好）。
                # - 点击时往右偏一点，落在输入区域内（避免点到图标前缀）。
                # - 验证是否进入了搜索页（出现"取消"或"历史搜索"）再继续。

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

                    # 优先用小 ROI 判定；能看到"取消"或"历史搜索"就可以认为进入了搜索页。
                    boxes2 = await self.ui.ocr_screenshot_advanced(
                        path2,
                        roi=KUGOU_ROIS["top_search_verify"],
                        scale=3.2,
                        grayscale=True,
                        accurate=False,
                        custom_words=["搜索", "取消", "历史", "历史搜索"],
                    )
                    has_cancel = _has_text(boxes2, "取消")

                    # A1（按你的补充）：topROI 内只要出现“搜索 / 搜 / 索”任一即可。
                    has_search_like = (
                        _has_text(boxes2, "搜索")
                        or _has_text(boxes2, "搜")
                        or _has_text(boxes2, "索")
                    )

                    # “历史搜索”在 OCR 下可能被误识别成“5史搜索”等变体：
                    # - 优先匹配完整词
                    # - 其次允许“历史 +（搜索/搜/索）”的拆词
                    has_history_like = bool(
                        _has_text(boxes2, "历史搜索")
                        or (
                            _has_text(boxes2, "历史")
                            and (
                                _has_text(boxes2, "搜索")
                                or _has_text(boxes2, "搜")
                                or _has_text(boxes2, "索")
                            )
                        )
                    )

                    focus_info: Optional[Dict[str, Any]] = None
                    focus_is_text = False
                    if not (has_cancel or has_history_like or has_search_like):
                        # 方案2：OCR 不稳时，使用“焦点元素角色”兜底判定是否已进入搜索输入态。
                        try:
                            focus_info = await self.ui.get_focused_ui_element_info("酷狗音乐")
                        except Exception:
                            focus_info = None

                        role = str((focus_info or {}).get("role") or "")
                        focus_is_text = bool((focus_info or {}).get("ok") is True and ("Text" in role or "Field" in role))

                    ok = bool(has_cancel or has_history_like or has_search_like or focus_is_text)
                    debug_info.setdefault("searchEntryVerify", []).append(
                        {
                            "tag": tag,
                            "ok": bool(ok),
                            "hasCancel": bool(has_cancel),
                            "hasHistory": bool(has_history_like),
                            "hasSearchLike": bool(has_search_like),
                            "focused": focus_info,
                            "preview": [str(getattr(b, "text", "") or "") for b in boxes2[:10]],
                        }
                    )
                    return (bool(ok), cap2, boxes2)

                # 1) 在 search_bar ROI 内做 OCR
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

                    # 稍微往右偏移一点，避免点到图标前缀，直接落在输入区域内。
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

                # 2) 如果 OCR 识别不到"搜索"文本（只有图标的 UI），就点右上角的搜索图标，
                # 然后通过"取消/历史搜索"来验证是否进入了搜索页。
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
                    # 3) 兜底：在 search_bar ROI 内按固定坐标点击（有些版本会显示文本搜索框）。
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

                    # 多试几次还是进不了搜索页，就尝试关闭可能的遮挡弹窗再继续。
                    if enter_search_fallback_tries >= 2:
                        try:
                            await _close_overlay_panel()
                        except Exception as e:
                            debug_info.setdefault("warnings", []).append(f"疑似遮挡层关闭失败（忽略继续）：{e}")

                    continue

                continue

            # 未知状态：先尝试顶部返回（可能是"分类"这类子页），然后重新检测；若还不行就再点侧边栏"音乐"。
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
                debug_info.setdefault("warnings", []).append(f"重试点击'音乐'失败（idx={step}）：{e}")
                await asyncio.sleep(0.25)
        else:
            _dump_debug_info("error_converge_failed", error="未能收敛到可搜索界面")
            raise RuntimeError("未能收敛到可搜索界面（search view / results page）")

        # 3) 聚焦搜索输入框（锚定"搜索"或"取消"），然后提交查询。
        if float(iw) <= 1 or float(ih) <= 1:
            raise RuntimeError("无法读取截图尺寸，无法执行搜索")

        roi_top = _roi_px(KUGOU_ROIS["top_search"], iw=iw, ih=ih)
        cancel_box = _find_best_box(boxes, target="取消", roi_px=roi_top, min_conf=detect_min_confidence)
        search_box = _find_best_box(boxes, target="搜索", roi_px=roi_top, min_conf=detect_min_confidence, prefer_top_left=True)

        if search_box is not None:
            await _click_box_center(cap, search_box, step="focus_search")
        elif cancel_box is not None:
            # 点"取消"左侧一点的位置来聚焦输入框。
            await _click_anchor_offset(cap, anchor_box=cancel_box, dx=-160.0, dy=0.0, step="focus_input_left_of_cancel")
        else:
            # 有时 placeholder "搜索"识别不出来，就在 top_search ROI 内兜底点一下。
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
        await self.ui.key_code(36)  # 回车
        await asyncio.sleep(0.85)

        # 4) 验证是否进入了搜索结果页。
        cap, boxes, iw, ih = await _capture_full("kugou_after_submit")
        state = _detect_state(boxes, iw=iw, ih=ih)
        debug_info.setdefault("flow", []).append({"step": "after_submit", "state": state})
        _dump_debug_info("after_submit")
        if state.get("mode") != "results_page":
            _dump_debug_info("error_not_results_page", error=str(state))
            raise RuntimeError(f"未能进入搜索结果页：{state}")

        # 5) 点击"单曲"tab。
        roi_tabs = _roi_px(KUGOU_ROIS["tabs"], iw=iw, ih=ih)
        tab_song = _find_best_box(boxes, target="单曲", roi_px=roi_tabs, min_conf=detect_min_confidence, prefer_top_left=True)
        if tab_song is None:
            raise RuntimeError("已进入结果页，但未识别到'单曲'入口")
        await _click_box_center(cap, tab_song, step="tab_song")
        await asyncio.sleep(0.35)

        # 6) 点击目标歌名（单击），然后通过底部播放栏验证播放状态。
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
            """落盘单曲列表 OCR 证据（boxes + ROI 裁剪图），用于事后排查。

            这是个辅助函数，内部出错时不应掩盖主流程的异常。
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

                # 1) 落盘 boxes JSON（按置信度排序，只取前 N 条方便阅读）。
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

                # 2) 落盘 ROI 裁剪图（可视化证据）。
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

        # 在结果列表区域做 OCR；用专门的 ROI（不修改全局 KUGOU_ROIS）。
        # 原因：全局的 result_list_top 是给其他步骤调的，可能会裁掉标题左侧。
        song_list_roi = (0.06, 0.20, 0.90, 0.50)

        def _song_list_is_loading(boxes_any: List[Any]) -> bool:
            for b in boxes_any:
                t = _norm(str(getattr(b, "text", "") or ""))
                if ("加载中" in t) or ("请稍候" in t) or ("稍候" in t):
                    return True
            return False

        # 方案1：单曲列表 OCR 前增加“加载态等待”。
        # 说明：点击“单曲”后 UI 可能短暂出现“加载中，请稍候”，如果立刻 OCR 会被误判为无结果。
        list_boxes: List[Any] = []
        for attempt in range(12):
            tag = f"kugou_song_list_wait_{attempt}"
            if attempt == 11:
                tag = "kugou_song_list"

            cap = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=tag)
            debug_info.setdefault("captures", []).append({"step": tag, "capture": cap})

            path = str(cap.get("screenshotPath") or "")
            if not path:
                raise RuntimeError("无法获取截图路径，无法在结果列表中定位歌曲")

            list_boxes = await self.ui.ocr_screenshot_advanced(
                path,
                roi=song_list_roi,
                scale=2.4,
                grayscale=True,
                accurate=False,
                custom_words=[song_key, "VIP", "MV", "播放", "暂停", "加载中", "请稍候", "稍候"],
            )

            loading = _song_list_is_loading(list_boxes)
            debug_info.setdefault("songListWait", []).append(
                {
                    "attempt": int(attempt),
                    "tag": tag,
                    "loading": bool(loading),
                    "preview": [str(getattr(b, "text", "") or "") for b in list_boxes[:12]],
                }
            )
            if not loading:
                break

            await asyncio.sleep(0.25)

        best = None
        best_key = None
        for b in list_boxes:
            text = str(getattr(b, "text", "") or "").strip()
            if not text:
                continue

            t_norm = _norm(text)
            if not t_norm:
                continue

            # 避免点到"歌手：xxx"这类标题行，除非其文本也包含目标歌名。
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

            # 优先选最靠前的匹配行，再按置信度/相似度排序。
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

        # 注意：不通过底部播放栏验证播放状态。
        # 底部播放条可能不会稳定出现，且进度 OCR 容易不准。
        await asyncio.sleep(0.3)

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
                    # 跳过页面顶部区域（避免点到标题/搜索框）。
                    continue

            out.append(b)
        return out

