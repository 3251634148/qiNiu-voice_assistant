from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import struct
import subprocess
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class ScreenSize:
    width: int
    height: int


@dataclass(frozen=True)
class WindowBounds:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class OcrBox:
    """OCR 结果框（截图图像坐标系，左上角为原点）。"""

    text: str
    confidence: float
    x: float
    y: float
    width: float
    height: float

    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)


class MacOSUIAutomation:
    """macOS UI automation helper.

    Implementation strategy:
    - Screenshot via `screencapture` (built-in)
    - OCR via macOS Vision framework (PyObjC)
    - Mouse click via Quartz CGEvent (PyObjC)
    - Keyboard input via AppleScript (System Events)

    Notes:
    - Requires macOS Accessibility permission for the current process.
    - OCR requires `pyobjc-core`, `pyobjc-framework-Cocoa`, `pyobjc-framework-Quartz`, `pyobjc-framework-Vision`.
    - This is a "best effort" engine; apps with self-drawn UI may still be brittle.
    """

    def __init__(self) -> None:
        self._ensure_darwin()

    @staticmethod
    def _ensure_darwin() -> None:
        import platform

        if platform.system().lower() != "darwin":
            raise RuntimeError("MacOSUIAutomation 仅支持 macOS")

    @staticmethod
    def _osascript(script: str) -> str:
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "osascript 执行失败")
        return proc.stdout.strip()

    @staticmethod
    def _escape_applescript_string(value: str) -> str:
        return str(value).replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _debug_dir() -> Path:
        """Return debug output directory.

        If env var `VOICE_ASSISTANT_DEBUG_RUN` is set, write logs into a per-run sub-directory,
        so each request's artifacts are isolated.
        """

        base = Path.home() / "Documents" / "VoiceAssistant" / "ui_debug"

        run_id = str(os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or "").strip()
        if run_id:
            safe = re.sub(r"[^0-9a-zA-Z_.-]+", "_", run_id).strip("_")
            safe = safe[:120] if safe else ""
            if safe:
                base = base / safe

        base.mkdir(parents=True, exist_ok=True)
        return base

    @staticmethod
    def _is_png_lossless_compress_enabled() -> bool:
        v = str(os.environ.get("VOICE_ASSISTANT_PNG_LOSSLESS_COMPRESS", "1") or "").strip().lower()
        return v not in {"0", "false", "no", "off"}

    @staticmethod
    def _lossless_recompress_png_zlib_sync(png_path: str) -> dict[str, Any]:
        """Losslessly recompress a PNG by re-deflating the IDAT stream.

        This keeps pixels identical (we do NOT touch scanlines), but may reduce file size.
        If recompression does not reduce size, the original file is kept.

        Returns a small metadata dict suitable for ui_debug.
        """

        path = Path(str(png_path))
        if not path.exists() or not path.is_file():
            return {"ok": False, "skipped": True, "reason": "not_a_file", "path": str(path)}

        before_bytes = path.stat().st_size
        if before_bytes <= 0:
            return {"ok": False, "skipped": True, "reason": "empty_file", "path": str(path)}

        try:
            raw = path.read_bytes()
        except Exception as e:
            return {"ok": False, "skipped": True, "reason": "read_failed", "error": str(e), "path": str(path)}

        sig = b"\x89PNG\r\n\x1a\n"
        if not raw.startswith(sig):
            return {"ok": False, "skipped": True, "reason": "not_png", "path": str(path)}

        # 解析 PNG chunk。
        offset = len(sig)
        idat_parts: list[bytes] = []
        chunks: list[tuple[bytes, bytes, bool]] = []  # (type, raw_chunk_bytes, is_idat)
        saw_idat = False
        is_apng = False

        while offset + 8 <= len(raw):
            try:
                length = struct.unpack(">I", raw[offset : offset + 4])[0]
            except Exception:
                break

            ctype = raw[offset + 4 : offset + 8]
            chunk_start = offset
            data_start = offset + 8
            data_end = data_start + int(length)
            crc_end = data_end + 4
            if crc_end > len(raw):
                break

            chunk_bytes = raw[chunk_start:crc_end]
            data = raw[data_start:data_end]

            # 避免处理 APNG（动图 PNG），这里仅处理静态 PNG。
            if ctype in {b"acTL", b"fcTL", b"fdAT"}:
                is_apng = True

            if ctype == b"IDAT":
                saw_idat = True
                idat_parts.append(data)
                # 保留占位符以便后续重建文件时保持原始 chunk 顺序。
                chunks.append((ctype, b"", True))
            else:
                chunks.append((ctype, chunk_bytes, False))

            offset = crc_end
            if ctype == b"IEND":
                break

        if is_apng:
            return {
                "ok": False,
                "skipped": True,
                "reason": "apng_not_supported",
                "beforeBytes": before_bytes,
                "path": str(path),
            }

        if not saw_idat or not idat_parts:
            return {
                "ok": False,
                "skipped": True,
                "reason": "no_idat",
                "beforeBytes": before_bytes,
                "path": str(path),
            }

        idat_raw = b"".join(idat_parts)

        try:
            inflated = zlib.decompress(idat_raw)
        except Exception as e:
            return {
                "ok": False,
                "skipped": True,
                "reason": "idat_inflate_failed",
                "error": str(e),
                "beforeBytes": before_bytes,
                "path": str(path),
            }

        try:
            recompressed = zlib.compress(inflated, level=9)
        except Exception as e:
            return {
                "ok": False,
                "skipped": True,
                "reason": "idat_deflate_failed",
                "error": str(e),
                "beforeBytes": before_bytes,
                "path": str(path),
            }

        def _pack_chunk(chunk_type: bytes, payload: bytes) -> bytes:
            length_bytes = struct.pack(">I", len(payload))
            crc = zlib.crc32(chunk_type)
            crc = zlib.crc32(payload, crc)
            crc_bytes = struct.pack(">I", crc & 0xFFFFFFFF)
            return length_bytes + chunk_type + payload + crc_bytes

        # 重建 PNG：保持原始 chunk 顺序，但把所有 IDAT 合并为“首个 IDAT 位置的一段重压缩 IDAT”。
        out_parts: list[bytes] = [sig]
        inserted = False
        for ctype, chunk_bytes, is_idat_chunk in chunks:
            if is_idat_chunk:
                if not inserted:
                    out_parts.append(_pack_chunk(b"IDAT", recompressed))
                    inserted = True
                continue

            out_parts.append(chunk_bytes)

        if not inserted:
            # 极端兜底：如果意外没看到 IDAT 占位符，就在结尾前补一个 IDAT。
            out_parts.append(_pack_chunk(b"IDAT", recompressed))

        new_raw = b"".join(out_parts)
        tmp_path = path.with_suffix(path.suffix + ".tmp")

        try:
            tmp_path.write_bytes(new_raw)
        except Exception as e:
            return {
                "ok": False,
                "skipped": True,
                "reason": "write_tmp_failed",
                "error": str(e),
                "beforeBytes": before_bytes,
                "path": str(path),
            }

        after_bytes = tmp_path.stat().st_size

        # 仅当新文件更小才替换（保持像素不变，避免无意义改写）。
        replaced = False
        if after_bytes < before_bytes:
            try:
                tmp_path.replace(path)
                replaced = True
            except Exception as e:
                try:
                    tmp_path.unlink(missing_ok=True)  # type: ignore[call-arg]
                except Exception:
                    pass
                return {
                    "ok": False,
                    "skipped": True,
                    "reason": "replace_failed",
                    "error": str(e),
                    "beforeBytes": before_bytes,
                    "afterBytes": after_bytes,
                    "path": str(path),
                }
        else:
            try:
                tmp_path.unlink(missing_ok=True)  # type: ignore[call-arg]
            except Exception:
                pass

        final_bytes = path.stat().st_size
        return {
            "ok": True,
            "enabled": True,
            "replaced": bool(replaced),
            "beforeBytes": before_bytes,
            "afterBytes": final_bytes,
            "savedBytes": int(before_bytes - final_bytes),
            "path": str(path),
        }

    async def lossless_compress_png(self, png_path: str) -> dict[str, Any]:
        """在后台线程对 PNG 做无损重压缩（尽力而为）。"""

        if not self._is_png_lossless_compress_enabled():
            return {"ok": True, "enabled": False, "skipped": True, "reason": "disabled", "path": str(png_path)}

        return await asyncio.to_thread(self._lossless_recompress_png_zlib_sync, str(png_path))

    @staticmethod
    def _get_main_screen_size() -> ScreenSize:
        try:
            from AppKit import NSScreen

            frame = NSScreen.mainScreen().frame()
            return ScreenSize(width=int(frame.size.width), height=int(frame.size.height))
        except Exception as e:
            raise RuntimeError(f"无法读取屏幕尺寸：{e}")

    @staticmethod
    def _get_global_desktop_max_y() -> float:
        """Return global desktop max Y in AppKit coordinates (origin bottom-left).

        Why this matters
        - `kCGWindowBounds` uses a global coordinate space whose Y behaves like bottom-left origin.
        - Mouse events (`CGEventCreateMouseEvent`) use a global event coordinate space whose Y behaves like top-left origin.
        - On multi-monitor setups, you cannot use only the main screen height; you must use the global desktop maxY.
        """

        try:
            from AppKit import NSScreen

            screens = NSScreen.screens() or []
            if not screens:
                return float(MacOSUIAutomation._get_main_screen_size().height)

            max_y = max(float(s.frame().origin.y + s.frame().size.height) for s in screens)
            return float(max_y)
        except Exception:
            return float(MacOSUIAutomation._get_main_screen_size().height)

    @staticmethod
    def _get_screens_snapshot_sync() -> dict[str, Any]:
        """返回当前显示器 frame 快照（AppKit 坐标系，左下角为原点）。"""

        def _infer_global_max_y() -> Optional[dict[str, Any]]:
            """Infer the Y-bridge between Quartz event-space and AppKit global coords.

            We can sample the *same* physical cursor position via two APIs:
            - Quartz (event-space): origin top-left, y increases downward
            - AppKit (global): origin bottom-left, y increases upward

            For a stable desktop, their sum is a constant: globalMaxY = appkitY + quartzY.
            """

            try:
                from AppKit import NSEvent
                from Quartz import CGEventCreate, CGEventGetLocation
            except Exception:
                return None

            try:
                appkit_pt = NSEvent.mouseLocation()
                quartz_pt = CGEventGetLocation(CGEventCreate(None))
            except Exception:
                return None

            try:
                appkit = {"x": float(appkit_pt.x), "y": float(appkit_pt.y)}
                quartz = {"x": float(quartz_pt.x), "y": float(quartz_pt.y)}
                inferred = float(appkit["y"]) + float(quartz["y"])
                return {"ok": True, "appkitMouse": appkit, "quartzMouse": quartz, "globalMaxY": inferred}
            except Exception:
                return None

        inferred = _infer_global_max_y()

        try:
            from AppKit import NSScreen

            screens = NSScreen.screens() or []
            items: list[dict[str, Any]] = []
            for idx, s in enumerate(list(screens)):
                frame = s.frame()
                visible = s.visibleFrame()

                screen_number = None
                try:
                    desc = s.deviceDescription() or {}
                    screen_number = desc.get("NSScreenNumber")
                except Exception:
                    screen_number = None

                items.append(
                    {
                        "index": int(idx),
                        "screenNumber": int(screen_number) if isinstance(screen_number, (int, float)) else screen_number,
                        "frame": {
                            "x": float(frame.origin.x),
                            "y": float(frame.origin.y),
                            "width": float(frame.size.width),
                            "height": float(frame.size.height),
                        },
                        "visibleFrame": {
                            "x": float(visible.origin.x),
                            "y": float(visible.origin.y),
                            "width": float(visible.size.width),
                            "height": float(visible.size.height),
                        },
                    }
                )

            max_y = None
            if items:
                max_y = max(float(i["frame"]["y"]) + float(i["frame"]["height"]) for i in items)

            payload: dict[str, Any] = {
                "ok": True,
                "screens": items,
                "globalMaxYByScreens": float(max_y) if max_y is not None else None,
                "globalMaxYInferred": inferred.get("globalMaxY") if isinstance(inferred, dict) else None,
                "inferMeta": inferred,
            }
            return payload
        except Exception as e:
            return {"ok": False, "error": str(e), "screens": [], "inferMeta": inferred}

    @staticmethod
    def _pick_screen_for_appkit_point(
        appkit_x: float,
        appkit_y: float,
        *,
        screens: Sequence[Mapping[str, Any]],
    ) -> Optional[dict[str, Any]]:
        for item in screens:
            frame = item.get("frame") or {}
            fx = float(frame.get("x") or 0.0)
            fy = float(frame.get("y") or 0.0)
            fw = float(frame.get("width") or 0.0)
            fh = float(frame.get("height") or 0.0)
            if fw <= 0 or fh <= 0:
                continue

            if fx <= float(appkit_x) <= fx + fw and fy <= float(appkit_y) <= fy + fh:
                return dict(item)

        return None

    async def screenshot(self, *, tag: str = "screen") -> str:
        """截取当前屏幕并保存为 PNG，返回文件路径。"""

        out_path = self._debug_dir() / f"{tag}_{int(time.time() * 1000)}.png"

        def _run() -> None:
            proc = subprocess.run(["screencapture", "-x", str(out_path)], capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or "screencapture 执行失败")

            if self._is_png_lossless_compress_enabled():
                # 尽力而为：保持像素完全一致，但可能降低文件体积。
                self._lossless_recompress_png_zlib_sync(str(out_path))

        await asyncio.to_thread(_run)
        return str(out_path)

    @staticmethod
    def _parse_window_bounds(bounds: Mapping[str, Any]) -> WindowBounds:
        def _get(key: str) -> float:
            v = bounds.get(key)
            try:
                return float(v)
            except Exception:
                return 0.0

        # Quartz 的 kCGWindowBounds 通常包含键：X/Y/Width/Height。
        x = _get("X")
        y = _get("Y")
        w = _get("Width")
        h = _get("Height")
        return WindowBounds(x=x, y=y, width=w, height=h)

    @staticmethod
    def _get_image_size_sync(image_path: str) -> ScreenSize:
        try:
            from AppKit import NSImage
        except Exception as e:
            raise RuntimeError(f"无法读取截图尺寸：{e}")

        image = NSImage.alloc().initWithContentsOfFile_(str(image_path))
        if image is None:
            raise RuntimeError("无法读取截图文件")

        w = int(image.size().width)
        h = int(image.size().height)
        if w <= 1 or h <= 1:
            raise RuntimeError("截图尺寸无效")

        return ScreenSize(width=w, height=h)

    @staticmethod
    def _normalize_owner_name(value: str) -> str:
        v = str(value or "").strip().lower()
        # 归一化：去掉空白与标点，仅保留字母数字与中日韩字符。
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", v)

    @staticmethod
    def _run_cmd_capture_sync(cmd: Sequence[str], *, timeout_sec: float = 3.0) -> Dict[str, Any]:
        try:
            proc = subprocess.run(
                list(cmd),
                capture_output=True,
                text=True,
                timeout=float(timeout_sec),
            )
            return {
                "cmd": list(cmd),
                "returncode": proc.returncode,
                "stdout": (proc.stdout or "").strip(),
                "stderr": (proc.stderr or "").strip(),
            }
        except Exception as e:
            return {"cmd": list(cmd), "error": str(e)}

    @classmethod
    def _dump_windows_snapshot_sync(
        cls,
        windows: Sequence[Any],
        *,
        tag: str,
        meta: Optional[Mapping[str, Any]] = None,
        limit: int = 500,
    ) -> str:
        out_path = cls._debug_dir() / f"windows_{tag}_{int(time.time() * 1000)}.json"

        try:
            input_len = len(windows)  # type: ignore[arg-type]
        except Exception:
            input_len = None

        items: list[dict[str, Any]] = []
        owners: list[str] = []
        for info in list(windows)[: max(0, int(limit))]:
            # Quartz via PyObjC returns NSDictionary/NSCFDictionary, which is not a native `dict`.
            if not hasattr(info, "get"):
                continue

            owner = str(info.get("kCGWindowOwnerName") or "")
            owners.append(owner)

            bounds_raw = info.get("kCGWindowBounds")
            bounds_payload: Optional[dict[str, Any]] = None
            if hasattr(bounds_raw, "get"):
                bounds_payload = {
                    "X": bounds_raw.get("X"),
                    "Y": bounds_raw.get("Y"),
                    "Width": bounds_raw.get("Width"),
                    "Height": bounds_raw.get("Height"),
                }

            items.append(
                {
                    "ownerName": owner,
                    "ownerNameNormalized": cls._normalize_owner_name(owner),
                    "windowName": str(info.get("kCGWindowName") or ""),
                    "windowId": info.get("kCGWindowNumber"),
                    "ownerPid": info.get("kCGWindowOwnerPID"),
                    "layer": info.get("kCGWindowLayer"),
                    "alpha": info.get("kCGWindowAlpha"),
                    "isOnscreen": info.get("kCGWindowIsOnscreen"),
                    "bounds": bounds_payload,
                }
            )

        uniq_owners = sorted({o for o in owners if o})
        payload = {
            "meta": {
                "tag": tag,
                "timestampMs": int(time.time() * 1000),
                "pid": os.getpid(),
                "ppid": os.getppid(),
                "uid": getattr(os, "getuid", lambda: None)(),
                "euid": getattr(os, "geteuid", lambda: None)(),
                "python": sys.executable,
                "argv0": sys.argv[0] if sys.argv else None,
                "platform": platform.platform(),
                "windowsInputType": str(type(windows)),
                "windowsInputLen": input_len,
                "uniqueOwnersCount": len(uniq_owners),
                "uniqueOwnersSample": uniq_owners[:80],
                **(dict(meta or {})),
            },
            "items": items,
        }

        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(out_path)

    @classmethod
    def _dump_window_probe_sync(
        cls,
        *,
        owner_names: Sequence[str],
        tokens: Sequence[str],
        windows_on_screen: Sequence[Any],
        windows_all: Sequence[Any],
    ) -> str:
        out_path = cls._debug_dir() / f"window_probe_{int(time.time() * 1000)}.json"

        probe = {
            "meta": {
                "timestampMs": int(time.time() * 1000),
                "pid": os.getpid(),
                "python": sys.executable,
                "platform": platform.platform(),
                "ownerNames": list(owner_names),
                "ownerTokens": list(tokens),
            },
            "process": {
                "ps_kugou": cls._run_cmd_capture_sync(["/bin/zsh", "-lc", "ps -ax | egrep -i 'kugou|kgmusic' | head -n 50"], timeout_sec=3.0),
                "pgrep_kugou": cls._run_cmd_capture_sync(["/bin/zsh", "-lc", "pgrep -fl 'kugou|kgmusic' | head -n 50"], timeout_sec=3.0),
                "osascript_frontmost": cls._run_cmd_capture_sync(
                    [
                        "osascript",
                        "-e",
                        'tell application "System Events" to get name of first application process whose frontmost is true',
                    ],
                    timeout_sec=3.0,
                ),
                "osascript_processes": cls._run_cmd_capture_sync(
                    ["osascript", "-e", 'tell application "System Events" to get name of application processes'],
                    timeout_sec=5.0,
                ),
            },
            "windowCounts": {
                "onScreen": len(list(windows_on_screen)),
                "all": len(list(windows_all)),
            },
        }

        out_path.write_text(json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(out_path)

    @classmethod
    def _find_best_window_sync(cls, owner_names: Sequence[str]) -> Tuple[int, WindowBounds, str]:
        try:
            from Quartz import (
                CGWindowListCopyWindowInfo,
                kCGNullWindowID,
                kCGWindowListExcludeDesktopElements,
                kCGWindowListOptionAll,
                kCGWindowListOptionOnScreenOnly,
            )
        except Exception as e:
            raise RuntimeError(f"无法读取窗口列表（Quartz 不可用）：{e}")

        tokens = {cls._normalize_owner_name(str(n)) for n in owner_names if str(n).strip()}
        tokens = {t for t in tokens if t}
        if not tokens:
            raise RuntimeError("owner_names 不能为空")

        def _owner_match(owner_name: str) -> bool:
            o = cls._normalize_owner_name(owner_name)
            if not o:
                return False
            # 优先用“包含”匹配，例如让 “Kugou Music” 能匹配到 “KugouMusic”。
            for t in tokens:
                if not t:
                    continue
                if t == o:
                    return True
                if len(t) >= 3 and t in o:
                    return True
                if len(o) >= 3 and o in t:
                    return True
            return False

        windows = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []

        def _pick_candidates(window_list: Sequence[Any]) -> list[tuple[float, int, WindowBounds, str]]:
            candidates: list[tuple[float, int, WindowBounds, str]] = []
            for info in window_list:
                # Quartz via PyObjC returns NSDictionary/NSCFDictionary, which is not a native `dict`.
                if not hasattr(info, "get"):
                    continue

                owner = str(info.get("kCGWindowOwnerName") or "").strip()
                if not _owner_match(owner):
                    continue

                try:
                    layer = int(info.get("kCGWindowLayer") or 0)
                except Exception:
                    layer = 0
                # 部分应用窗口不一定在 layer=0；这里保持保守但不过度严格。
                if layer < 0 or layer > 2:
                    continue

                bounds_dict = info.get("kCGWindowBounds")
                if not hasattr(bounds_dict, "get"):
                    continue

                bounds = cls._parse_window_bounds(bounds_dict)
                if bounds.width < 120 or bounds.height < 120:
                    continue

                try:
                    alpha = float(info.get("kCGWindowAlpha") or 1.0)
                except Exception:
                    alpha = 1.0
                if alpha <= 0.01:
                    continue

                window_id = info.get("kCGWindowNumber")
                try:
                    wid = int(window_id)
                except Exception:
                    continue

                is_onscreen_raw = info.get("kCGWindowIsOnscreen")
                is_onscreen = bool(is_onscreen_raw is True or is_onscreen_raw == 1)

                area = bounds.width * bounds.height
                score = area * (1.15 if is_onscreen else 1.0) * max(0.2, min(alpha, 1.0))
                candidates.append((score, wid, bounds, owner))

            return candidates

        candidates = _pick_candidates(windows)
        if not candidates:
            # 兜底：扩大枚举范围，但仍排除桌面元素。
            all_windows = CGWindowListCopyWindowInfo(
                int(kCGWindowListOptionAll) | int(kCGWindowListExcludeDesktopElements),
                kCGNullWindowID,
            ) or []
            candidates = _pick_candidates(all_windows)
            if not candidates:
                probe_path = cls._dump_window_probe_sync(
                    owner_names=owner_names,
                    tokens=sorted(tokens),
                    windows_on_screen=windows,
                    windows_all=all_windows,
                )
                snap_on_screen = cls._dump_windows_snapshot_sync(
                    windows,
                    tag="no_match_on_screen",
                    meta={"probePath": probe_path, "note": "kCGWindowListOptionOnScreenOnly"},
                )
                snap_all = cls._dump_windows_snapshot_sync(
                    all_windows,
                    tag="no_match_all",
                    meta={"probePath": probe_path, "note": "kCGWindowListOptionAll|ExcludeDesktop"},
                )

                if not list(all_windows):
                    raise RuntimeError(
                        "Quartz 返回 0 个窗口信息，无法识别任何应用窗口。"
                        "这通常是因为运行后端的进程没有获得‘屏幕录制(Screen Recording)’权限，"
                        "或当前进程不在登录用户的图形会话里（例如被 launchd/service 启动）。"
                        f"已导出探针：{probe_path}；窗口快照：{snap_on_screen} / {snap_all}"
                    )

                raise RuntimeError(
                    f"未找到可见窗口：{list(owner_names)}。已导出探针：{probe_path}；窗口快照：{snap_on_screen} / {snap_all}"
                )

        _, wid, bounds, owner = max(candidates, key=lambda x: x[0])
        return (wid, bounds, owner)

    async def screenshot_window(self, *, owner_names: Sequence[str], tag: str = "window") -> Dict[str, Any]:
        """Capture a specific app window to a PNG and return capture metadata.

        This avoids capturing the wrong screen/window (e.g. IDE) when multiple monitors are in use.
        """

        out_path = self._debug_dir() / f"{tag}_{int(time.time() * 1000)}.png"

        def _run() -> Dict[str, Any]:
            wid, bounds, owner = self._find_best_window_sync(owner_names)
            # 注意：必须禁用窗口阴影（-o），否则 PNG 会多出阴影像素，破坏 `windowBounds` 与 `imageSize` 的线性映射。
            proc = subprocess.run(
                ["screencapture", "-l", str(wid), "-x", "-o", str(out_path)],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or "screencapture(窗口) 执行失败")

            compress_meta: Optional[dict[str, Any]] = None
            if self._is_png_lossless_compress_enabled():
                compress_meta = self._lossless_recompress_png_zlib_sync(str(out_path))

            image_size = self._get_image_size_sync(str(out_path))
            return {
                "screenshotPath": str(out_path),
                "ownerName": owner,
                "windowId": wid,
                "windowBounds": {
                    "x": bounds.x,
                    "y": bounds.y,
                    "width": bounds.width,
                    "height": bounds.height,
                },
                "imageSize": {"width": image_size.width, "height": image_size.height},
                "pngLosslessCompress": compress_meta,
            }

        return await asyncio.to_thread(_run)

    @staticmethod
    def _to_screen_point_from_window_image_point(
        x: float,
        y: float,
        *,
        window_bounds: Mapping[str, Any],
        image_size: Mapping[str, Any],
    ) -> tuple[float, float]:
        """Convert window screenshot image point (origin top-left) to CGEvent mouse point.

        Why this matters
        - OCR boxes and screenshot pixels use a top-left origin (y increases downward).
        - Quartz window bounds (from `CGWindowListCopyWindowInfo`) are in a global space that behaves like
          a bottom-left origin for `kCGWindowBounds`.
        - `CGEventCreateMouseEvent` expects points in the Quartz event coordinate space where the main
          display origin is at **top-left** (y increases downward).

        So we must:
        1) Map screenshot pixels -> window-local points (handle Retina scaling)
        2) Convert window bounds bottom-left y -> event-space top-left y
        """

        wb = WindowBounds(
            x=float(window_bounds.get("x") or 0.0),
            y=float(window_bounds.get("y") or 0.0),
            width=float(window_bounds.get("width") or 0.0),
            height=float(window_bounds.get("height") or 0.0),
        )
        iw = float(image_size.get("width") or 0.0)
        ih = float(image_size.get("height") or 0.0)

        if iw <= 1 or ih <= 1 or wb.width <= 1 or wb.height <= 1:
            # 兜底：假设 1:1，并把 window_bounds.y 当作已处于事件坐标系。
            return (wb.x + float(x), wb.y + float(y))

        scale_x = wb.width / iw
        scale_y = wb.height / ih

        # 1) Convert screenshot pixels to window-local points (origin top-left).
        x_local = float(x) * scale_x
        y_local = float(y) * scale_y

        # 2) Convert window bounds to event-space (top-left).
        # Empirically, `windowBounds` from `CGWindowListCopyWindowInfo` aligns with Quartz event-space
        # (origin top-left, y increases downward) on this project setup. Treat `wb.y` as the window top.
        window_top_in_event = float(wb.y)

        sx = wb.x + x_local
        sy = window_top_in_event + y_local
        return (sx, sy)

    @staticmethod
    def _to_window_image_point_from_screen_point(
        sx: float,
        sy: float,
        *,
        window_bounds: Mapping[str, Any],
        image_size: Mapping[str, Any],
    ) -> tuple[float, float]:
        """Convert CGEvent mouse point (origin top-left) back to window screenshot image point.

        This is the inverse mapping of `_to_screen_point_from_window_image_point`.
        It is primarily used for debug hit-testing: proving whether a click target lands inside
        a specific OCR box in the window screenshot.
        """

        wb = WindowBounds(
            x=float(window_bounds.get("x") or 0.0),
            y=float(window_bounds.get("y") or 0.0),
            width=float(window_bounds.get("width") or 0.0),
            height=float(window_bounds.get("height") or 0.0),
        )
        iw = float(image_size.get("width") or 0.0)
        ih = float(image_size.get("height") or 0.0)

        if iw <= 1 or ih <= 1 or wb.width <= 1 or wb.height <= 1:
            # 尽力而为的兜底。
            return (float(sx) - float(wb.x), float(sy) - float(wb.y))

        scale_x = wb.width / iw
        scale_y = wb.height / ih
        if scale_x <= 0 or scale_y <= 0:
            return (0.0, 0.0)

        window_top_in_event = float(wb.y)

        x_local = float(sx) - wb.x
        y_local = float(sy) - window_top_in_event

        x_img = x_local / scale_x
        y_img = y_local / scale_y
        return (float(x_img), float(y_img))

    @staticmethod
    def _import_vision() -> Any:
        try:
            from AppKit import NSImage  # noqa: F401
            from Vision import VNRecognizeTextRequest  # noqa: F401

            return True
        except Exception as e:
            raise RuntimeError(
                "OCR 依赖未安装或不可用：请安装 PyObjC Vision 相关依赖（backend_py/requirements.txt）。\n"
                f"原始错误：{e}"
            )

    @staticmethod
    def _ocr_image_sync(image_path: str) -> List[OcrBox]:
        """Run OCR on the image and return text boxes.

        Returns boxes in image coordinates with origin at top-left.
        """

        MacOSUIAutomation._import_vision()

        from AppKit import NSImage
        from Foundation import NSURL
        from Vision import VNImageRequestHandler, VNRecognizeTextRequest

        url = NSURL.fileURLWithPath_(str(image_path))
        image = NSImage.alloc().initWithContentsOfURL_(url)
        if image is None:
            raise RuntimeError("无法读取截图文件")

        w = float(image.size().width)
        h = float(image.size().height)
        if w <= 1 or h <= 1:
            raise RuntimeError("截图尺寸无效")

        results: list[OcrBox] = []

        def _handler(request: Any, error: Any) -> None:
            if error is not None:
                raise RuntimeError(str(error))

        req = VNRecognizeTextRequest.alloc().initWithCompletionHandler_(_handler)
        req.setRecognitionLevel_(1)  # accurate
        req.setUsesLanguageCorrection_(True)

        # 提升中文 UI 的 OCR 识别质量。
        try:
            req.setRecognitionLanguages_(["zh-Hans", "zh-Hant", "en-US"])
        except Exception:
            # 某些 macOS/PyObjC 版本可能不暴露该 setter。
            pass

        # 通过常见 UI 词表对 OCR 做偏置，提升命中率。
        try:
            req.setCustomWords_(["搜索", "我的", "收藏", "我喜欢", "喜欢", "歌单", "单曲", "歌曲", "播放", "暂停"])
        except Exception:
            pass

        handler = VNImageRequestHandler.alloc().initWithURL_options_(url, None)
        ok = handler.performRequests_error_([req], None)
        if not ok:
            raise RuntimeError("Vision OCR 执行失败")

        observations = req.results() or []
        for obs in observations:
            candidates = obs.topCandidates_(1)
            if not candidates:
                continue
            best = candidates[0]
            text = str(best.string() or "").strip()
            if not text:
                continue

            confidence = float(best.confidence())
            bb = obs.boundingBox()  # normalized, origin at lower-left
            x_ll = float(bb.origin.x) * w
            y_ll = float(bb.origin.y) * h
            bw = float(bb.size.width) * w
            bh = float(bb.size.height) * h

            # 转换为左上角原点坐标
            x = x_ll
            y = h - y_ll - bh
            results.append(OcrBox(text=text, confidence=confidence, x=x, y=y, width=bw, height=bh))

        return results

    async def ocr_screenshot(self, image_path: str) -> List[OcrBox]:
        return await asyncio.to_thread(self._ocr_image_sync, image_path)

    @staticmethod
    def _load_cgimage_sync(image_path: str) -> tuple[Any, int, int]:
        """加载图片为 CGImage，并返回 (cgimage, width, height)。"""

        MacOSUIAutomation._import_vision()

        from Foundation import NSURL
        from Quartz import (
            CGImageGetHeight,
            CGImageGetWidth,
            CGImageSourceCreateImageAtIndex,
            CGImageSourceCreateWithURL,
        )

        url = NSURL.fileURLWithPath_(str(image_path))
        src = CGImageSourceCreateWithURL(url, None)
        if src is None:
            raise RuntimeError(f"无法加载图片：{image_path}")

        cg = CGImageSourceCreateImageAtIndex(src, 0, None)
        if cg is None:
            raise RuntimeError(f"无法获取 CGImage：{image_path}")

        w = int(CGImageGetWidth(cg))
        h = int(CGImageGetHeight(cg))
        if w <= 1 or h <= 1:
            raise RuntimeError("截图尺寸无效")

        return (cg, w, h)

    @staticmethod
    def _crop_cgimage_sync(
        cgimage: Any,
        *,
        image_w: int,
        image_h: int,
        roi: tuple[float, float, float, float],
    ) -> tuple[Any, float, float, int, int]:
        """Crop CGImage by normalized ROI (x, y, width, height), origin top-left.

        Returns:
            (cg_cropped, offset_x_px, offset_y_px, cropped_w_px, cropped_h_px)
        """

        from Quartz import CGImageCreateWithImageInRect, CGImageGetHeight, CGImageGetWidth

        rx, ry, rw, rh = roi
        rx = max(0.0, min(1.0, float(rx)))
        ry = max(0.0, min(1.0, float(ry)))
        rw = max(0.0, min(1.0 - rx, float(rw)))
        rh = max(0.0, min(1.0 - ry, float(rh)))

        x = float(rx) * float(image_w)
        y = float(ry) * float(image_h)
        ww = float(rw) * float(image_w)
        hh = float(rh) * float(image_h)

        rect = ((x, y), (ww, hh))
        cropped = CGImageCreateWithImageInRect(cgimage, rect)
        if cropped is None:
            return (cgimage, 0.0, 0.0, int(image_w), int(image_h))

        cw = int(CGImageGetWidth(cropped))
        ch = int(CGImageGetHeight(cropped))
        return (cropped, float(x), float(y), cw, ch)

    @staticmethod
    def _preprocess_cgimage_sync(
        cgimage: Any,
        *,
        scale: float = 1.0,
        grayscale: bool = True,
    ) -> tuple[Any, float]:
        """Lightweight OCR preprocessing using Quartz/CoreGraphics only.

        - Scale up to help small-font OCR
        - Optional grayscale to reduce color noise

        Returns:
            (cg_processed, effective_scale)
        """

        from Quartz import (
            CGBitmapContextCreate,
            CGColorSpaceCreateDeviceGray,
            CGColorSpaceCreateDeviceRGB,
            CGContextDrawImage,
            CGContextSetInterpolationQuality,
            CGImageGetHeight,
            CGImageGetWidth,
            CGRectMake,
            kCGInterpolationHigh,
        )

        try:
            w0 = int(CGImageGetWidth(cgimage))
            h0 = int(CGImageGetHeight(cgimage))
        except Exception:
            return (cgimage, 1.0)

        if w0 <= 1 or h0 <= 1:
            return (cgimage, 1.0)

        s = max(1.0, float(scale))
        w = int(w0 * s)
        h = int(h0 * s)

        if grayscale:
            cs = CGColorSpaceCreateDeviceGray()
            bpc = 8
            bytes_per_row = w
            bitmap_info = 0
        else:
            cs = CGColorSpaceCreateDeviceRGB()
            bpc = 8
            bytes_per_row = w * 4
            bitmap_info = 0

        ctx = CGBitmapContextCreate(None, w, h, bpc, bytes_per_row, cs, bitmap_info)
        if ctx is None:
            return (cgimage, 1.0)

        CGContextSetInterpolationQuality(ctx, kCGInterpolationHigh)
        CGContextDrawImage(ctx, CGRectMake(0, 0, float(w), float(h)), cgimage)

        try:
            cg2 = ctx.makeImage()
            return (cg2 or cgimage, s)
        except Exception:
            return (cgimage, 1.0)

    @staticmethod
    def _ocr_cgimage_sync(
        cgimage: Any,
        *,
        languages: Optional[Sequence[str]] = None,
        accurate: bool = True,
        language_correction: bool = True,
        min_text_height: Optional[float] = None,
        custom_words: Optional[Sequence[str]] = None,
    ) -> List[OcrBox]:
        """对 CGImage 执行 Vision OCR，并返回图像像素坐标系（左上角原点）的文本框列表。"""

        MacOSUIAutomation._import_vision()

        from Quartz import CGImageGetHeight, CGImageGetWidth
        from Vision import VNImageRequestHandler, VNRecognizeTextRequest

        w = float(CGImageGetWidth(cgimage))
        h = float(CGImageGetHeight(cgimage))
        if w <= 1 or h <= 1:
            raise RuntimeError("截图尺寸无效")

        results: list[OcrBox] = []

        def _handler(request: Any, error: Any) -> None:
            if error is not None:
                raise RuntimeError(str(error))

        req = VNRecognizeTextRequest.alloc().initWithCompletionHandler_(_handler)
        req.setRecognitionLevel_(1 if bool(accurate) else 0)
        req.setUsesLanguageCorrection_(bool(language_correction))

        if languages:
            try:
                req.setRecognitionLanguages_(list(languages))
            except Exception:
                pass

        if custom_words:
            try:
                req.setCustomWords_(list(custom_words))
            except Exception:
                pass

        if min_text_height is not None:
            try:
                req.setMinimumTextHeight_(float(min_text_height))
            except Exception:
                pass

        handler = VNImageRequestHandler.alloc().initWithCGImage_options_(cgimage, None)
        ok = handler.performRequests_error_([req], None)
        if not ok:
            raise RuntimeError("Vision OCR 执行失败")

        observations = req.results() or []
        for obs in observations:
            candidates = obs.topCandidates_(1)
            if not candidates:
                continue
            best = candidates[0]
            text = str(best.string() or "").strip()
            if not text:
                continue

            confidence = float(best.confidence())
            bb = obs.boundingBox()  # normalized, origin at lower-left
            x_ll = float(bb.origin.x) * w
            y_ll = float(bb.origin.y) * h
            bw = float(bb.size.width) * w
            bh = float(bb.size.height) * h

            x = x_ll
            y = h - y_ll - bh
            results.append(OcrBox(text=text, confidence=confidence, x=x, y=y, width=bw, height=bh))

        return results

    @classmethod
    def _ocr_image_advanced_sync(
        cls,
        image_path: str,
        *,
        roi: Optional[tuple[float, float, float, float]] = None,
        scale: float = 2.4,
        grayscale: bool = True,
        accurate: bool = True,
        language_correction: bool = True,
        min_text_height: Optional[float] = None,
        languages: Optional[Sequence[str]] = None,
        custom_words: Optional[Sequence[str]] = None,
    ) -> List[OcrBox]:
        """Advanced OCR with ROI cropping + preprocessing.

        Follows the approach used by `test_scripts/debug_ocr_vision_kugou.py`.

        Returns boxes in ORIGINAL image pixel coordinates (origin top-left).
        """

        cg, w0, h0 = cls._load_cgimage_sync(str(image_path))

        x_off = 0.0
        y_off = 0.0
        if roi is not None:
            cg, x_off, y_off, _, _ = cls._crop_cgimage_sync(cg, image_w=w0, image_h=h0, roi=roi)

        cg2, eff_scale = cls._preprocess_cgimage_sync(cg, scale=float(scale), grayscale=bool(grayscale))

        boxes_scaled = cls._ocr_cgimage_sync(
            cg2,
            languages=languages or ["zh-Hans", "zh-Hant", "en-US"],
            accurate=bool(accurate),
            language_correction=bool(language_correction),
            min_text_height=min_text_height,
            custom_words=custom_words,
        )

        if not boxes_scaled:
            return []

        scale_back = float(eff_scale) if float(eff_scale) > 0 else 1.0
        boxes: list[OcrBox] = []
        for b in boxes_scaled:
            boxes.append(
                OcrBox(
                    text=b.text,
                    confidence=b.confidence,
                    x=float(x_off) + float(b.x) / scale_back,
                    y=float(y_off) + float(b.y) / scale_back,
                    width=float(b.width) / scale_back,
                    height=float(b.height) / scale_back,
                )
            )

        return boxes

    async def ocr_screenshot_advanced(
        self,
        image_path: str,
        *,
        roi: Optional[tuple[float, float, float, float]] = None,
        scale: float = 2.4,
        grayscale: bool = True,
        accurate: bool = True,
        language_correction: bool = True,
        min_text_height: Optional[float] = None,
        languages: Optional[Sequence[str]] = None,
        custom_words: Optional[Sequence[str]] = None,
    ) -> List[OcrBox]:
        return await asyncio.to_thread(
            self._ocr_image_advanced_sync,
            image_path,
            roi=roi,
            scale=float(scale),
            grayscale=bool(grayscale),
            accurate=bool(accurate),
            language_correction=bool(language_correction),
            min_text_height=min_text_height,
            languages=languages,
            custom_words=custom_words,
        )

    @staticmethod
    def _to_screen_point_from_image_point(x: float, y: float, *, screen: ScreenSize) -> tuple[float, float]:
        """Convert image point (origin top-left) to CGEvent mouse point (origin top-left).

        Notes:
        - This helper is only used for full-screen screenshots (non window-targeted).
        - It does not account for Retina scaling; prefer window-targeted flow when possible.
        """

        # Quartz 事件坐标系使用左上角原点。
        return (float(x), float(y))

    @staticmethod
    def _click_at_sync(x: float, y: float, *, clicks: int = 1, interval_sec: float = 0.12) -> None:
        """在 Quartz 事件坐标系指定点点击（左上角原点）。"""
        try:
            from Quartz import (
                CGEventCreateMouseEvent,
                CGEventPost,
                CGPoint,
                kCGEventLeftMouseDown,
                kCGEventLeftMouseUp,
                kCGHIDEventTap,
            )
        except Exception as e:
            raise RuntimeError(
                "鼠标事件依赖未安装或不可用：请安装 pyobjc-framework-Quartz。\n"
                f"原始错误：{e}"
            )

        pt = CGPoint(x, y)
        for idx in range(max(1, int(clicks))):
            down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, pt, 0)
            up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, pt, 0)
            CGEventPost(kCGHIDEventTap, down)
            CGEventPost(kCGHIDEventTap, up)
            if idx < clicks - 1:
                time.sleep(interval_sec)

    async def click_at(self, x: float, y: float, *, clicks: int = 1) -> None:
        await asyncio.to_thread(self._click_at_sync, x, y, clicks=clicks)

    @staticmethod
    def _scroll_wheel_sync(
        *,
        delta_y: int,
        unit: str = "line",
    ) -> None:
        """Post a vertical scroll wheel event.

        Args:
            delta_y: Positive/negative scroll amount. The direction can vary by app and system settings,
                so callers should use OCR/state detection to validate.
            unit: "line" (default) or "pixel".
        """

        try:
            from Quartz import (
                CGEventCreateScrollWheelEvent,
                CGEventPost,
                kCGHIDEventTap,
                kCGScrollEventUnitLine,
                kCGScrollEventUnitPixel,
            )
        except Exception as e:
            raise RuntimeError(
                "滚动事件依赖未安装或不可用：请安装 pyobjc-framework-Quartz。\n"
                f"原始错误：{e}"
            )

        u = kCGScrollEventUnitLine if str(unit or "line").lower() == "line" else kCGScrollEventUnitPixel
        ev = CGEventCreateScrollWheelEvent(None, u, 1, int(delta_y))
        if ev is None:
            raise RuntimeError("无法创建滚动事件")
        CGEventPost(kCGHIDEventTap, ev)

    async def scroll_wheel(
        self,
        *,
        delta_y: int,
        steps: int = 1,
        unit: str = "line",
        interval_sec: float = 0.05,
    ) -> None:
        """Scroll vertically by posting wheel events (best-effort).

        This is primarily used for UI automation flows where the app does not expose a stable API.
        """

        n = max(1, int(steps))
        for idx in range(n):
            await asyncio.to_thread(self._scroll_wheel_sync, delta_y=int(delta_y), unit=str(unit))
            if idx < n - 1:
                await asyncio.sleep(float(interval_sec))

    @staticmethod
    def _get_mouse_position_sync() -> dict[str, float]:
        """Read current mouse cursor position.

        Returns a point in the Quartz event coordinate space used by `CGEventCreateMouseEvent`
        (typically origin at top-left for the main display; multi-monitor space follows the
        global desktop event space).
        """

        try:
            from Quartz import CGEventCreate, CGEventGetLocation
        except Exception as e:
            raise RuntimeError(
                "鼠标位置读取依赖未安装或不可用：请安装 pyobjc-framework-Quartz。\n"
                f"原始错误：{e}"
            )

        ev = CGEventCreate(None)
        if ev is None:
            raise RuntimeError("无法创建 CGEvent 用于读取鼠标位置")

        pt = CGEventGetLocation(ev)
        return {"x": float(pt.x), "y": float(pt.y)}

    async def get_mouse_position(self) -> dict[str, float]:
        """异步读取当前鼠标光标位置。"""

        return await asyncio.to_thread(self._get_mouse_position_sync)

    async def probe_mouse_position(self, *, tag: str = "mouse_probe") -> dict[str, Any]:
        """Read current mouse position and persist it to ui_debug.

        This is a debug-only helper intended for evidence gathering.
        """

        pt = await self.get_mouse_position()
        out_path = self._debug_dir() / f"{str(tag).strip() or 'mouse_probe'}_{int(time.time() * 1000)}.json"
        payload: dict[str, Any] = {
            "meta": {"tag": tag, "timestampMs": int(time.time() * 1000)},
            "mouse": pt,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"mouse": pt, "path": str(out_path)}

    @staticmethod
    def _warp_mouse_sync(x: float, y: float) -> None:
        """将真实鼠标光标移动到事件坐标系的指定点（尽力而为）。"""

        try:
            from Quartz import CGAssociateMouseAndMouseCursorPosition, CGWarpMouseCursorPosition, CGPoint
        except Exception as e:
            raise RuntimeError(
                "鼠标移动依赖未安装或不可用：请安装 pyobjc-framework-Quartz。\n"
                f"原始错误：{e}"
            )

        # 确保鼠标与光标处于关联状态（避免曾被系统/工具解耦）。
        try:
            CGAssociateMouseAndMouseCursorPosition(True)
        except Exception:
            pass

        pt = CGPoint(float(x), float(y))
        try:
            CGWarpMouseCursorPosition(pt)
        except Exception:
            # 兜底：尝试在主屏移动光标。
            try:
                from Quartz import CGDisplayMoveCursorToPoint, CGMainDisplayID

                CGDisplayMoveCursorToPoint(CGMainDisplayID(), pt)
            except Exception as e:
                raise RuntimeError(f"无法移动鼠标光标：{e}")

    async def warp_mouse(self, x: float, y: float) -> None:
        """异步移动真实鼠标光标。"""

        await asyncio.to_thread(self._warp_mouse_sync, float(x), float(y))

    async def click_at_debug(
        self,
        x: float,
        y: float,
        *,
        clicks: int = 1,
        tag: str = "click_debug",
        warp_cursor: bool = True,
        settle_sec: float = 0.02,
        window_bounds: Optional[Mapping[str, Any]] = None,
        image_size: Optional[Mapping[str, Any]] = None,
        ocr_box: Optional[Mapping[str, Any]] = None,
        cursor_shot: bool = False,
    ) -> dict[str, Any]:
        """Click with evidence.

        This helper is intended for debugging/probing so that "where did we click" can be proven by:
        - moving the real cursor to the target point (optional)
        - sampling the mouse position around the click
        - (optional) mapping the target point back to window screenshot coordinates and hit-testing
          against a provided OCR box.

        Notes:
        - In Quartz, posting mouse events does not always move the system cursor. If you need cursor
          position to be a reliable witness, set `warp_cursor=True`.
        """

        ts = int(time.time() * 1000)
        target = {"x": float(x), "y": float(y)}

        screen_meta: Optional[dict[str, Any]] = None
        try:
            snapshot = self._get_screens_snapshot_sync()

            def _as_float(v: Any) -> Optional[float]:
                try:
                    return float(v)
                except Exception:
                    return None

            global_by_screens = _as_float(snapshot.get("globalMaxYByScreens"))
            global_inferred = _as_float(snapshot.get("globalMaxYInferred"))
            global_used = global_inferred or global_by_screens or float(self._get_global_desktop_max_y())

            appkit_x = float(x)
            appkit_y_used = float(global_used) - float(y)
            appkit_y_by_screens = float(global_by_screens) - float(y) if global_by_screens is not None else None
            appkit_y_by_inferred = float(global_inferred) - float(y) if global_inferred is not None else None

            screens = snapshot.get("screens") or []

            def _build_picked(appkit_y: Optional[float]) -> Optional[dict[str, Any]]:
                if appkit_y is None:
                    return None
                if not isinstance(screens, list) or not screens:
                    return None

                picked = self._pick_screen_for_appkit_point(appkit_x, float(appkit_y), screens=screens)
                if picked is None:
                    return None

                frame = picked.get("frame") or {}
                fx = float(frame.get("x") or 0.0)
                fy = float(frame.get("y") or 0.0)
                fh = float(frame.get("height") or 0.0)

                local_bl_x = float(appkit_x) - fx
                local_bl_y = float(appkit_y) - fy
                return {
                    "screenIndex": picked.get("index"),
                    "screenNumber": picked.get("screenNumber"),
                    "frame": frame,
                    "localPointBottomLeft": {"x": local_bl_x, "y": local_bl_y},
                    "localPointTopLeft": {"x": local_bl_x, "y": float(fh) - local_bl_y},
                }

            picked_used = _build_picked(appkit_y_used)
            picked_by_screens = _build_picked(appkit_y_by_screens)
            picked_by_inferred = _build_picked(appkit_y_by_inferred)

            screen_meta = {
                "globalMaxYUsed": float(global_used),
                "globalMaxYByScreens": global_by_screens,
                "globalMaxYInferred": global_inferred,
                "inferMeta": snapshot.get("inferMeta"),
                "targetPointAppKitUsed": {"x": appkit_x, "y": float(appkit_y_used)},
                "targetPointAppKitByScreens": {"x": appkit_x, "y": float(appkit_y_by_screens)}
                if appkit_y_by_screens is not None
                else None,
                "targetPointAppKitByInferred": {"x": appkit_x, "y": float(appkit_y_by_inferred)}
                if appkit_y_by_inferred is not None
                else None,
                "screens": snapshot.get("screens") if snapshot.get("ok") is True else None,
                "pickedScreen": picked_used,
                "pickedScreenByScreens": picked_by_screens,
                "pickedScreenByInferred": picked_by_inferred,
                "note": "Quartz event coords: origin top-left; AppKit coords: origin bottom-left. globalMaxY is inferred by appkitY+quartzY when possible.",
            }
        except Exception as e:
            screen_meta = {"ok": False, "error": str(e)}

        frontmost_before: Optional[dict[str, Any]] = None
        try:
            frontmost_before = {"ok": True, "process": await self.get_frontmost_process_name()}
        except Exception as e:
            frontmost_before = {"ok": False, "error": str(e)}

        mouse_before = await self.get_mouse_position()

        cursor_shots_payload: Optional[dict[str, Any]] = None

        async def _capture_cursor_screenshot(phase: str) -> dict[str, Any]:
            out_path = self._debug_dir() / f"{str(tag).strip() or 'click_debug'}_{phase}_{ts}.png"

            def _run() -> dict[str, Any]:
                proc = subprocess.run(
                    ["screencapture", "-x", "-C", str(out_path)],
                    capture_output=True,
                    text=True,
                )
                if proc.returncode != 0:
                    return {
                        "ok": False,
                        "path": str(out_path),
                        "stderr": (proc.stderr or "").strip(),
                        "stdout": (proc.stdout or "").strip(),
                    }

                compress_meta: Optional[dict[str, Any]] = None
                if self._is_png_lossless_compress_enabled():
                    compress_meta = self._lossless_recompress_png_zlib_sync(str(out_path))

                return {"ok": True, "path": str(out_path), "pngLosslessCompress": compress_meta}

            return await asyncio.to_thread(_run)

        mouse_after_warp: Optional[dict[str, Any]] = None
        if bool(warp_cursor):
            try:
                await self.warp_mouse(float(x), float(y))
            except Exception as e:
                mouse_after_warp = {"ok": False, "error": str(e)}
            else:
                if float(settle_sec) > 0:
                    await asyncio.sleep(float(settle_sec))
                mouse_after_warp = {"ok": True, "mouse": await self.get_mouse_position()}

                if bool(cursor_shot):
                    cursor_shots_payload = cursor_shots_payload or {}
                    cursor_shots_payload["afterWarp"] = await _capture_cursor_screenshot("cursor_after_warp")

        await self.click_at(float(x), float(y), clicks=int(clicks))
        if float(settle_sec) > 0:
            await asyncio.sleep(float(settle_sec))

        frontmost_after: Optional[dict[str, Any]] = None
        try:
            frontmost_after = {"ok": True, "process": await self.get_frontmost_process_name()}
        except Exception as e:
            frontmost_after = {"ok": False, "error": str(e)}

        mouse_after_click = await self.get_mouse_position()

        if bool(cursor_shot):
            cursor_shots_payload = cursor_shots_payload or {}
            cursor_shots_payload["afterClick"] = await _capture_cursor_screenshot("cursor_after_click")

        def _delta(a: Optional[dict[str, float]], b: dict[str, float]) -> Optional[dict[str, float]]:
            if not a:
                return None
            try:
                return {"dx": float(a["x"]) - float(b["x"]), "dy": float(a["y"]) - float(b["y"])}
            except Exception:
                return None

        warp_mouse_pt = None
        if isinstance(mouse_after_warp, dict) and mouse_after_warp.get("ok") is True:
            warp_mouse_pt = mouse_after_warp.get("mouse")

        target_as_image_point: Optional[dict[str, float]] = None
        ocr_hit_test: Optional[dict[str, Any]] = None
        if window_bounds is not None and image_size is not None:
            try:
                ix, iy = self._to_window_image_point_from_screen_point(
                    float(x),
                    float(y),
                    window_bounds=window_bounds,
                    image_size=image_size,
                )
                target_as_image_point = {"x": float(ix), "y": float(iy)}

                if ocr_box is not None:
                    bx = float(ocr_box.get("x") or 0.0)
                    by = float(ocr_box.get("y") or 0.0)
                    bw = float(ocr_box.get("width") or 0.0)
                    bh = float(ocr_box.get("height") or 0.0)
                    in_box = (bx <= float(ix) <= bx + bw) and (by <= float(iy) <= by + bh)
                    ocr_hit_test = {
                        "ok": True,
                        "inBox": bool(in_box),
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
                if ocr_box is not None:
                    ocr_hit_test = {"ok": False, "error": str(e)}

        payload: dict[str, Any] = {
            "meta": {"tag": tag, "timestampMs": ts},
            "targetPoint": target,
            "screenMeta": screen_meta,
            "targetAsImagePoint": target_as_image_point,
            "ocrHitTest": ocr_hit_test,
            "frontmostBefore": frontmost_before,
            "frontmostAfter": frontmost_after,
            "mouseBefore": mouse_before,
            "mouseAfterWarp": mouse_after_warp,
            "mouseAfterClick": mouse_after_click,
            "cursorShots": cursor_shots_payload,
            "deltaAfterWarp": _delta(warp_mouse_pt, target),
            "deltaAfterClick": _delta(mouse_after_click, target),
            "warpCursor": bool(warp_cursor),
            "settleSec": float(settle_sec),
            "clicks": int(clicks),
            "windowMeta": {
                "windowBounds": dict(window_bounds or {}),
                "imageSize": dict(image_size or {}),
            }
            if window_bounds is not None and image_size is not None
            else None,
        }

        out_path = self._debug_dir() / f"{str(tag).strip() or 'click_debug'}_{ts}.json"
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(out_path), **payload}

    async def click_text(
        self,
        text: str,
        *,
        match_mode: str = "contains",
        min_confidence: float = 0.35,
        tag: str = "click_text",
        clicks: int = 1,
        window_owner_names: Optional[Sequence[str]] = None,
        roi: Optional[tuple[float, float, float, float]] = None,
        ocr_scale: float = 1.0,
        ocr_grayscale: bool = False,
        ocr_accurate: bool = True,
        ocr_language_correction: bool = True,
        ocr_min_text_height: Optional[float] = None,
        ocr_languages: Optional[Sequence[str]] = None,
        ocr_custom_words: Optional[Sequence[str]] = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Find text on screen via OCR and click it.

        Args:
            text: target text
            match_mode: "contains" or "exact"
            min_confidence: ignore low-confidence OCR results
            tag: screenshot tag
            clicks: number of clicks
            dry_run: if true, do not perform actual click
        """

        target = str(text or "").strip()
        if not target:
            raise RuntimeError("text 不能为空")

        capture: Optional[Dict[str, Any]] = None
        if window_owner_names:
            capture = await self.screenshot_window(owner_names=window_owner_names, tag=tag)
            screenshot_path = str(capture.get("screenshotPath") or "")
        else:
            screenshot_path = await self.screenshot(tag=tag)

        use_advanced = (
            roi is not None
            or bool(ocr_grayscale)
            or float(ocr_scale) != 1.0
            or ocr_min_text_height is not None
            or ocr_languages is not None
            or ocr_custom_words is not None
        )

        if use_advanced:
            boxes = await self.ocr_screenshot_advanced(
                screenshot_path,
                roi=roi,
                scale=float(ocr_scale),
                grayscale=bool(ocr_grayscale),
                accurate=bool(ocr_accurate),
                language_correction=bool(ocr_language_correction),
                min_text_height=ocr_min_text_height,
                languages=ocr_languages,
                custom_words=ocr_custom_words,
            )
        else:
            boxes = await self.ocr_screenshot(screenshot_path)

        def _norm(value: str) -> str:
            v = str(value or "")
            # 去除全部空白与常见替换字符，便于匹配。
            v = re.sub(r"\s+", "", v)
            v = v.replace("\uffff", "").replace("\ufffd", "")
            return v.strip().lower()

        target_norm = _norm(target)

        def _match(b: OcrBox) -> bool:
            if b.confidence < float(min_confidence):
                return False

            text_norm = _norm(b.text)
            if not text_norm:
                return False

            if match_mode == "exact":
                return text_norm == target_norm
            return target_norm in text_norm

        candidates = [b for b in boxes if _match(b)]
        if not candidates:
            # 落盘 OCR 结果用于排障。
            def _dump() -> str:
                out_path = self._debug_dir() / f"ocr_dump_{tag}_{int(time.time() * 1000)}.json"
                top = sorted(boxes, key=lambda x: x.confidence, reverse=True)[:60]
                payload = {
                    "meta": {
                        "tag": tag,
                        "timestampMs": int(time.time() * 1000),
                        "matchMode": match_mode,
                        "minConfidence": float(min_confidence),
                        "target": target,
                        "targetNormalized": target_norm,
                        "boxesTotal": len(boxes),
                        "ocr": {
                            "useAdvanced": use_advanced,
                            "roi": roi,
                            "scale": float(ocr_scale),
                            "grayscale": bool(ocr_grayscale),
                            "accurate": bool(ocr_accurate),
                            "languageCorrection": bool(ocr_language_correction),
                            "minTextHeight": ocr_min_text_height,
                            "languages": list(ocr_languages) if ocr_languages is not None else None,
                            "customWords": list(ocr_custom_words) if ocr_custom_words is not None else None,
                        },
                    },
                    "screenshotPath": screenshot_path,
                    "window": capture,
                    "boxes": [
                        {
                            "text": b.text,
                            "textNormalized": _norm(b.text),
                            "confidence": b.confidence,
                            "x": b.x,
                            "y": b.y,
                            "width": b.width,
                            "height": b.height,
                        }
                        for b in top
                    ],
                }
                out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                return str(out_path)

            dump_path = await asyncio.to_thread(_dump)
            preview = [b.text for b in sorted(boxes, key=lambda x: x.confidence, reverse=True)[:12]]
            raise RuntimeError(
                f"未在屏幕上找到文本：{target}（OCR 预览：{preview}）。已导出 OCR 明细：{dump_path}"
            )

        best = max(candidates, key=lambda b: (b.confidence, b.width * b.height))
        cx, cy = best.center()

        if capture:
            sx, sy = self._to_screen_point_from_window_image_point(
                cx,
                cy,
                window_bounds=capture.get("windowBounds") or {},
                image_size=capture.get("imageSize") or {},
            )
        else:
            screen = self._get_main_screen_size()
            sx, sy = self._to_screen_point_from_image_point(cx, cy, screen=screen)

        if not dry_run:
            await self.click_at(sx, sy, clicks=clicks)

        return {
            "screenshotPath": screenshot_path,
            "window": capture,
            "matched": {
                "text": best.text,
                "confidence": best.confidence,
                "imageBox": {"x": best.x, "y": best.y, "width": best.width, "height": best.height},
                "screenPoint": {"x": sx, "y": sy},
            },
            "dryRun": dry_run,
        }

    async def type_text(self, text: str, *, delay_sec: float = 0.02) -> None:
        """Type text using AppleScript System Events.

        This is less precise than key events but avoids keycode mapping.
        """

        value = str(text or "")
        # AppleScript 字符串转义
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "System Events" to keystroke "{escaped}"'
        self._osascript(script)
        if delay_sec > 0:
            await asyncio.sleep(float(delay_sec))

    async def hotkey(self, key: str, *, modifiers: Optional[Iterable[str]] = None) -> None:
        """按下组合键（例如 key='f', modifiers=['command down']）。"""

        k = str(key or "").strip()
        if not k:
            raise RuntimeError("key 不能为空")

        mods = list(modifiers or [])
        using = " using {" + ", ".join(mods) + "}" if mods else ""
        script = f'tell application "System Events" to keystroke "{k}"{using}'
        self._osascript(script)

    async def key_code(self, code: int) -> None:
        """通过 AppleScript 按下指定 keycode（例如回车 Return=36）。"""

        script = f'tell application "System Events" to key code {int(code)}'
        self._osascript(script)

    async def activate_app(self, app_name: str) -> None:
        name = str(app_name or "").strip()
        if not name:
            raise RuntimeError("app_name 不能为空")
        self._osascript(f'tell application "{name}" to activate')

    async def ensure_accessibility_ready(self) -> None:
        """尽力而为地提前检查辅助功能（Accessibility）权限是否缺失。"""

        try:
            self._osascript('tell application "System Events" to get name of processes')
        except Exception as e:
            raise RuntimeError(
                "当前进程可能未获得“辅助功能(Accessibility)”权限。\n"
                "请到 系统设置 -> 隐私与安全 -> 辅助功能，允许你的终端/运行后端的进程。\n"
                f"原始错误：{e}"
            )

    @staticmethod
    def file_sha256(file_path: str) -> str:
        try:
            h = hashlib.sha256()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 256), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return ""

    async def get_frontmost_process_name(self) -> str:
        """通过 System Events 获取当前前台应用进程名。"""

        return self._osascript(
            'tell application "System Events" to get name of first application process whose frontmost is true'
        )

    async def set_process_frontmost(self, process_name: str) -> None:
        """通过 System Events 强制置前某个进程（尽力而为）。"""

        name = str(process_name or "").strip()
        if not name:
            raise RuntimeError("process_name 不能为空")

        escaped = self._escape_applescript_string(name)
        script = (
            'tell application "System Events"\n'
            f'  set frontmost of process "{escaped}" to true\n'
            'end tell'
        )
        self._osascript(script)

    async def normalize_process_window(
        self,
        *,
        process_name: str,
        width: int,
        height: int,
        center_main_screen: bool = True,
    ) -> Dict[str, Any]:
        """Normalize a process's front window size/position.

        This is used to reduce OCR/ROI brittleness caused by window layout changes.
        """

        name = str(process_name or "").strip()
        if not name:
            raise RuntimeError("process_name 不能为空")

        w = int(width)
        h = int(height)
        if w <= 0 or h <= 0:
            raise RuntimeError(f"窗口尺寸无效：{w}x{h}")

        screen = self._get_main_screen_size()

        # AppleScript 的窗口位置通常使用全局屏幕坐标；这里保持保守，并把位置钳制到 >= 0。
        x = int(max(0, round((float(screen.width) - float(w)) / 2.0)))
        y = int(max(0, round((float(screen.height) - float(h)) / 2.0)))

        escaped = self._escape_applescript_string(name)

        if center_main_screen:
            script = (
                'tell application "System Events"\n'
                f'  tell process "{escaped}"\n'
                '    if not (exists window 1) then error "window 1 not found"\n'
                f'    set size of window 1 to {{{w}, {h}}}\n'
                f'    set position of window 1 to {{{x}, {y}}}\n'
                '  end tell\n'
                'end tell'
            )
        else:
            script = (
                'tell application "System Events"\n'
                f'  tell process "{escaped}"\n'
                '    if not (exists window 1) then error "window 1 not found"\n'
                f'    set size of window 1 to {{{w}, {h}}}\n'
                '  end tell\n'
                'end tell'
            )

        self._osascript(script)
        return {
            "ok": True,
            "process": name,
            "width": int(w),
            "height": int(h),
            "x": int(x),
            "y": int(y),
            "screen": {"width": int(screen.width), "height": int(screen.height)},
        }

    async def get_focused_ui_element_info(self, process_name: str) -> Dict[str, Any]:
        """尽力而为读取指定进程的当前焦点 UI 元素信息。"""

        name = str(process_name or "").strip()
        if not name:
            raise RuntimeError("process_name 不能为空")

        escaped = self._escape_applescript_string(name)
        script = (
            'tell application "System Events"\n'
            f'  tell process "{escaped}"\n'
            '    try\n'
            '      set r to role of focused UI element\n'
            '    on error errMsg\n'
            '      return "ERR|" & errMsg\n'
            '    end try\n'
            '    try\n'
            '      set d to description of focused UI element\n'
            '    on error\n'
            '      set d to ""\n'
            '    end try\n'
            '    try\n'
            '      set v to value of focused UI element\n'
            '    on error\n'
            '      set v to ""\n'
            '    end try\n'
            '    return (r & "|" & d & "|" & v)\n'
            '  end tell\n'
            'end tell'
        )

        raw = ""
        try:
            raw = self._osascript(script)
        except Exception as e:
            return {"ok": False, "error": str(e)}

        if str(raw).startswith("ERR|"):
            return {"ok": False, "error": str(raw)[4:]}

        parts = str(raw).split("|", 2)
        role = parts[0] if len(parts) >= 1 else ""
        desc = parts[1] if len(parts) >= 2 else ""
        value = parts[2] if len(parts) >= 3 else ""
        return {"ok": True, "role": role, "description": desc, "value": value}

    async def dump_process_menu(self, *, process_name: str, tag: str = "menu_dump") -> str:
        """导出指定进程的菜单结构到 ui_debug（用于排障）。"""

        name = str(process_name or "").strip()
        if not name:
            raise RuntimeError("process_name 不能为空")

        def _run() -> str:
            out_path = self._debug_dir() / f"{tag}_{int(time.time() * 1000)}.json"
            escaped = self._escape_applescript_string(name)

            script = (
                'tell application "System Events"\n'
                f'  tell process "{escaped}"\n'
                '    set outText to ""\n'
                '    try\n'
                '      set mbCount to (count of menu bar items of menu bar 1)\n'
                '    on error\n'
                '      set mbCount to 0\n'
                '    end try\n'
                '    repeat with i from 1 to mbCount\n'
                '      set mb to menu bar item i of menu bar 1\n'
                '      set mbName to (name of mb)\n'
                '      set outText to outText & "MB|" & i & "|" & mbName & "\\n"\n'
                '      try\n'
                '        set miCount to (count of menu items of menu 1 of mb)\n'
                '      on error\n'
                '        set miCount to 0\n'
                '      end try\n'
                '      repeat with j from 1 to miCount\n'
                '        set mi to menu item j of menu 1 of mb\n'
                '        set miName to (name of mi)\n'
                '        set outText to outText & "MI|" & i & "|" & j & "|" & miName & "\\n"\n'
                '        try\n'
                '          set siCount to (count of menu items of menu 1 of mi)\n'
                '        on error\n'
                '          set siCount to 0\n'
                '        end try\n'
                '        repeat with k from 1 to siCount\n'
                '          set si to menu item k of menu 1 of mi\n'
                '          set siName to (name of si)\n'
                '          set outText to outText & "SI|" & i & "|" & j & "|" & k & "|" & siName & "\\n"\n'
                '        end repeat\n'
                '      end repeat\n'
                '    end repeat\n'
                '    return outText\n'
                '  end tell\n'
                'end tell'
            )

            raw = ""
            err = None
            try:
                raw = self._osascript(script)
            except Exception as e:
                err = str(e)

            lines = [ln for ln in str(raw or "").splitlines() if ln.strip()]
            items: list[dict[str, Any]] = []
            for ln in lines[:5000]:
                parts = ln.split("|")
                if not parts:
                    continue
                kind = parts[0]
                if kind == "MB" and len(parts) >= 3:
                    items.append({"kind": "MB", "mbIndex": int(parts[1]), "name": "|".join(parts[2:])})
                elif kind == "MI" and len(parts) >= 4:
                    items.append(
                        {
                            "kind": "MI",
                            "mbIndex": int(parts[1]),
                            "miIndex": int(parts[2]),
                            "name": "|".join(parts[3:]),
                        }
                    )
                elif kind == "SI" and len(parts) >= 5:
                    items.append(
                        {
                            "kind": "SI",
                            "mbIndex": int(parts[1]),
                            "miIndex": int(parts[2]),
                            "siIndex": int(parts[3]),
                            "name": "|".join(parts[4:]),
                        }
                    )

            payload = {
                "meta": {
                    "tag": tag,
                    "timestampMs": int(time.time() * 1000),
                    "process": name,
                    "frontmostProcess": None,
                    "error": err,
                },
                "items": items,
            }
            try:
                payload["meta"]["frontmostProcess"] = self._osascript(
                    'tell application "System Events" to get name of first application process whose frontmost is true'
                )
            except Exception:
                pass

            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return str(out_path)

        return await asyncio.to_thread(_run)

    async def click_process_menu_item_contains(
        self,
        *,
        process_name: str,
        keywords: Sequence[str],
        tag: str = "menu_click",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """在指定进程菜单中查找并点击菜单项（或子菜单项）。"""

        name = str(process_name or "").strip()
        if not name:
            raise RuntimeError("process_name 不能为空")

        kws = [str(k).strip() for k in (keywords or []) if str(k).strip()]
        if not kws:
            raise RuntimeError("keywords 不能为空")

        escaped_name = self._escape_applescript_string(name)
        escaped = [self._escape_applescript_string(k) for k in kws]
        kw_list = "{" + ", ".join([f'"{k}"' for k in escaped]) + "}"

        click_stmt = "click" if not dry_run else "-- dry_run: skip click"
        script = (
            'tell application "System Events"\n'
            f'  tell process "{escaped_name}"\n'
            f'    set kwList to {kw_list}\n'
            '    try\n'
            '      set mbCount to (count of menu bar items of menu bar 1)\n'
            '    on error\n'
            '      set mbCount to 0\n'
            '    end try\n'
            '    repeat with i from 1 to mbCount\n'
            '      set mb to menu bar item i of menu bar 1\n'
            '      set mbName to (name of mb)\n'
            '      try\n'
            '        set miCount to (count of menu items of menu 1 of mb)\n'
            '      on error\n'
            '        set miCount to 0\n'
            '      end try\n'
            '      repeat with j from 1 to miCount\n'
            '        set mi to menu item j of menu 1 of mb\n'
            '        set miName to (name of mi)\n'
            '        repeat with kw in kwList\n'
            '          if miName contains (contents of kw) then\n'
            f'            {click_stmt} mi\n'
            '            return (mbName & " > " & miName)\n'
            '          end if\n'
            '        end repeat\n'
            '        try\n'
            '          set siCount to (count of menu items of menu 1 of mi)\n'
            '        on error\n'
            '          set siCount to 0\n'
            '        end try\n'
            '        repeat with k from 1 to siCount\n'
            '          set si to menu item k of menu 1 of mi\n'
            '          set siName to (name of si)\n'
            '          repeat with kw in kwList\n'
            '            if siName contains (contents of kw) then\n'
            f'              {click_stmt} si\n'
            '              return (mbName & " > " & miName & " > " & siName)\n'
            '            end if\n'
            '          end repeat\n'
            '        end repeat\n'
            '      end repeat\n'
            '    end repeat\n'
            '    return ""\n'
            '  end tell\n'
            'end tell'
        )

        path = ""
        error = None
        try:
            path = self._osascript(script)
        except Exception as e:
            error = str(e)

        if not str(path or "").strip():
            dump_path = await self.dump_process_menu(process_name=name, tag=f"{tag}_dump")
            raise RuntimeError(
                f"未在菜单中找到匹配项：{kws}。已导出菜单结构：{dump_path}" + (f"（错误：{error}）" if error else "")
            )

        return {"process": name, "keywords": kws, "matchedPath": str(path).strip(), "dryRun": dry_run}

    async def dump_frontmost_menu(self, *, tag: str = "menu_dump") -> str:
        """导出当前前台应用的菜单结构到 ui_debug（用于排障）。"""

        def _run() -> str:
            out_path = self._debug_dir() / f"{tag}_{int(time.time() * 1000)}.json"

            script = (
                'tell application "System Events"\n'
                '  tell (first application process whose frontmost is true)\n'
                '    set outText to ""\n'
                '    try\n'
                '      set mbCount to (count of menu bar items of menu bar 1)\n'
                '    on error\n'
                '      set mbCount to 0\n'
                '    end try\n'
                '    repeat with i from 1 to mbCount\n'
                '      set mb to menu bar item i of menu bar 1\n'
                '      set mbName to (name of mb)\n'
                '      set outText to outText & "MB|" & i & "|" & mbName & "\\n"\n'
                '      try\n'
                '        set miCount to (count of menu items of menu 1 of mb)\n'
                '      on error\n'
                '        set miCount to 0\n'
                '      end try\n'
                '      repeat with j from 1 to miCount\n'
                '        set mi to menu item j of menu 1 of mb\n'
                '        set miName to (name of mi)\n'
                '        set outText to outText & "MI|" & i & "|" & j & "|" & miName & "\\n"\n'
                '        try\n'
                '          set siCount to (count of menu items of menu 1 of mi)\n'
                '        on error\n'
                '          set siCount to 0\n'
                '        end try\n'
                '        repeat with k from 1 to siCount\n'
                '          set si to menu item k of menu 1 of mi\n'
                '          set siName to (name of si)\n'
                '          set outText to outText & "SI|" & i & "|" & j & "|" & k & "|" & siName & "\\n"\n'
                '        end repeat\n'
                '      end repeat\n'
                '    end repeat\n'
                '    return outText\n'
                '  end tell\n'
                'end tell'
            )

            raw = ""
            err = None
            try:
                raw = self._osascript(script)
            except Exception as e:
                err = str(e)

            lines = [ln for ln in str(raw or "").splitlines() if ln.strip()]
            items: list[dict[str, Any]] = []
            for ln in lines[:5000]:
                parts = ln.split("|")
                if not parts:
                    continue
                kind = parts[0]
                if kind == "MB" and len(parts) >= 3:
                    items.append({"kind": "MB", "mbIndex": int(parts[1]), "name": "|".join(parts[2:])})
                elif kind == "MI" and len(parts) >= 4:
                    items.append(
                        {
                            "kind": "MI",
                            "mbIndex": int(parts[1]),
                            "miIndex": int(parts[2]),
                            "name": "|".join(parts[3:]),
                        }
                    )
                elif kind == "SI" and len(parts) >= 5:
                    items.append(
                        {
                            "kind": "SI",
                            "mbIndex": int(parts[1]),
                            "miIndex": int(parts[2]),
                            "siIndex": int(parts[3]),
                            "name": "|".join(parts[4:]),
                        }
                    )

            payload = {
                "meta": {
                    "tag": tag,
                    "timestampMs": int(time.time() * 1000),
                    "frontmostProcess": None,
                    "error": err,
                },
                "items": items,
            }
            try:
                payload["meta"]["frontmostProcess"] = self._osascript(
                    'tell application "System Events" to get name of first application process whose frontmost is true'
                )
            except Exception:
                pass

            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return str(out_path)

        return await asyncio.to_thread(_run)

    async def click_frontmost_menu_item_contains(
        self,
        *,
        keywords: Sequence[str],
        tag: str = "menu_click",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """查找并点击名称包含任意关键词的菜单项（或子菜单项）。"""

        kws = [str(k).strip() for k in (keywords or []) if str(k).strip()]
        if not kws:
            raise RuntimeError("keywords 不能为空")

        escaped = [self._escape_applescript_string(k) for k in kws]
        kw_list = "{" + ", ".join([f'"{k}"' for k in escaped]) + "}"

        click_stmt = "click" if not dry_run else "-- dry_run: skip click"
        script = (
            'tell application "System Events"\n'
            '  tell (first application process whose frontmost is true)\n'
            f'    set kwList to {kw_list}\n'
            '    try\n'
            '      set mbCount to (count of menu bar items of menu bar 1)\n'
            '    on error\n'
            '      set mbCount to 0\n'
            '    end try\n'
            '    repeat with i from 1 to mbCount\n'
            '      set mb to menu bar item i of menu bar 1\n'
            '      set mbName to (name of mb)\n'
            '      try\n'
            '        set miCount to (count of menu items of menu 1 of mb)\n'
            '      on error\n'
            '        set miCount to 0\n'
            '      end try\n'
            '      repeat with j from 1 to miCount\n'
            '        set mi to menu item j of menu 1 of mb\n'
            '        set miName to (name of mi)\n'
            '        repeat with kw in kwList\n'
            '          if miName contains (contents of kw) then\n'
            f'            {click_stmt} mi\n'
            '            return (mbName & " > " & miName)\n'
            '          end if\n'
            '        end repeat\n'
            '        try\n'
            '          set siCount to (count of menu items of menu 1 of mi)\n'
            '        on error\n'
            '          set siCount to 0\n'
            '        end try\n'
            '        repeat with k from 1 to siCount\n'
            '          set si to menu item k of menu 1 of mi\n'
            '          set siName to (name of si)\n'
            '          repeat with kw in kwList\n'
            '            if siName contains (contents of kw) then\n'
            f'              {click_stmt} si\n'
            '              return (mbName & " > " & miName & " > " & siName)\n'
            '            end if\n'
            '          end repeat\n'
            '        end repeat\n'
            '      end repeat\n'
            '    end repeat\n'
            '    return ""\n'
            '  end tell\n'
            'end tell'
        )

        path = ""
        error = None
        try:
            path = self._osascript(script)
        except Exception as e:
            error = str(e)

        if not str(path or "").strip():
            dump_path = await self.dump_frontmost_menu(tag=f"{tag}_dump")
            raise RuntimeError(f"未在菜单中找到匹配项：{kws}。已导出菜单结构：{dump_path}" + (f"（错误：{error}）" if error else ""))

        return {
            "keywords": kws,
            "matchedPath": str(path).strip(),
            "dryRun": dry_run,
        }

    async def click_window_relative(
        self,
        *,
        owner_names: Sequence[str],
        x_ratio: float,
        y_ratio_from_top: float,
        clicks: int = 1,
    ) -> Dict[str, Any]:
        """Click a point in the target window by relative ratios.

        - x_ratio: 0..1 from left
        - y_ratio_from_top: 0..1 from top

        Returns `screenPoint` in **CGEvent mouse coordinates** (origin top-left).
        """

        xr = max(0.0, min(1.0, float(x_ratio)))
        yr = max(0.0, min(1.0, float(y_ratio_from_top)))

        def _calc() -> Dict[str, Any]:
            wid, bounds, owner = self._find_best_window_sync(owner_names)
            global_max_y = float(self._get_global_desktop_max_y())
            window_top_in_event = global_max_y - (bounds.y + bounds.height)

            sx = bounds.x + bounds.width * xr
            sy = window_top_in_event + bounds.height * yr
            return {
                "ownerName": owner,
                "windowId": wid,
                "windowBounds": {"x": bounds.x, "y": bounds.y, "width": bounds.width, "height": bounds.height},
                "screenPoint": {"x": sx, "y": sy},
            }

        info = await asyncio.to_thread(_calc)
        await self.click_at(float(info["screenPoint"]["x"]), float(info["screenPoint"]["y"]), clicks=clicks)
        return info
