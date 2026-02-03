# -*- coding: utf-8 -*-
"""KuGou search box click calibration.

目的：用自动化方式扫描“顶部可能是搜索框”的区域，逐点：点击 -> 粘贴固定文本 -> 截图。
通过截图证据判断：哪些点能把文本送进搜索框（或能打开搜索入口/页面）。

输出：
- 所有截图与结果 JSON 会落在：~/Documents/VoiceAssistant/ui_debug/<run_id>/

使用：
VOICE_ASSISTANT_DEBUG_RUN=<run_id> python test_scripts/debug_kugou_searchbox_calib.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List

# Ensure project root is importable when running as a script.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend_py.services.macos_ui_automation import MacOSUIAutomation
from backend_py.services.music_controller import KUGOU_APP_NAMES


def _set_clipboard_text(text: str) -> None:
    """Set macOS clipboard text."""

    subprocess.run(["pbcopy"], input=str(text), text=True, check=False)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate KuGou search box click points")
    parser.add_argument(
        "--text",
        default="abc123",
        help="Text to paste at each point (default: abc123)",
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="If provided and VOICE_ASSISTANT_DEBUG_RUN is empty, set it to this value.",
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=180,
        help="Delay after click/paste steps in milliseconds (default: 180)",
    )
    parser.add_argument(
        "--x",
        default="0.55,0.62,0.69,0.76,0.83,0.90",
        help="Comma-separated x_ratio grid values (0..1)",
    )
    parser.add_argument(
        "--y",
        default="0.05,0.07,0.09,0.11,0.13",
        help="Comma-separated y_ratio_from_top grid values (0..1)",
    )
    parser.add_argument(
        "--try-exit-detail",
        action="store_true",
        help="Before scanning, click top-left area to try exiting song detail page.",
    )
    parser.add_argument(
        "--cursor-shot",
        action="store_true",
        help="After each click, capture a full-screen screenshot with cursor for verification.",
    )
    return parser.parse_args()


def _parse_grid(values: str) -> List[float]:
    out: List[float] = []
    for part in str(values or "").split(","):
        p = part.strip()
        if not p:
            continue
        try:
            out.append(float(p))
        except Exception:
            continue
    return out


async def main() -> int:
    args = _parse_args()

    if not os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") and args.run_id:
        os.environ["VOICE_ASSISTANT_DEBUG_RUN"] = str(args.run_id).strip()

    run_id = os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or ""
    if not run_id:
        os.environ["VOICE_ASSISTANT_DEBUG_RUN"] = f"calib_kugou_searchbox_{int(time.time())}"

    ui = MacOSUIAutomation()
    await ui.ensure_accessibility_ready()

    # Bring KuGou to front.
    subprocess.run(["open", "-a", "酷狗音乐"], capture_output=True, text=True, check=False)
    try:
        await ui.activate_app("酷狗音乐")
    except Exception:
        pass
    try:
        await ui.set_process_frontmost("酷狗音乐")
    except Exception:
        pass

    await asyncio.sleep(0.6)

    results: List[Dict[str, Any]] = []
    base = ui._debug_dir()  # pylint: disable=protected-access

    # Baseline capture before any attempt.
    start_cap = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="calib_start")
    results.append({"step": "start", "capture": start_cap})

    # Optional: try exit song detail page.
    # 说明：在“歌曲/评论/相关”的详情页，左上角会有一个向下箭头（返回）。
    # 先尝试一组常见“返回/退出”快捷键，再用小网格点击去定位可用的返回按钮坐标。
    if args.try_exit_detail:
        key_attempts = [
            {"name": "escape", "fn": lambda: ui.key_code(53)},
            {"name": "cmd_left_bracket", "fn": lambda: ui.hotkey("[", modifiers=["command down"])},
            {"name": "cmd_1", "fn": lambda: ui.hotkey("1", modifiers=["command down"])},
        ]
        for item in key_attempts:
            name = str(item.get("name") or "")
            entry: Dict[str, Any] = {"step": "exit_detail_key", "name": name, "ok": True}
            try:
                await item["fn"]()  # type: ignore[index]
            except Exception as e:
                entry["ok"] = False
                entry["error"] = str(e)
            await asyncio.sleep(0.25)
            try:
                entry["capture"] = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=f"exit_key_{name}")
            except Exception as e:
                entry["capture"] = {"error": str(e), "tag": f"exit_key_{name}"}
            results.append(entry)

        # NOTE: 根据校准结果，按键（如 Escape / Cmd+[ / Cmd+1）可以稳定从详情页回到首页。
        # 这里不再进行“左上角网格点击返回”以避免误点首页卡片导致再次进入详情页。

    # Re-capture after exit attempts.
    post_exit_cap = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="calib_after_exit")
    results.append({"step": "after_exit", "capture": post_exit_cap})

    xs = _parse_grid(args.x)
    ys = _parse_grid(args.y)

    sleep_sec = max(0.02, float(args.sleep_ms) / 1000.0)
    idx = 0

    for y_ratio in ys:
        for x_ratio in xs:
            _set_clipboard_text(args.text)
            entry: Dict[str, Any] = {
                "index": idx,
                "xRatio": x_ratio,
                "yRatioFromTop": y_ratio,
                "focus": None,
                "click": None,
                "capture": None,
            }

            try:
                entry["click"] = await ui.click_window_relative(
                    owner_names=KUGOU_APP_NAMES,
                    x_ratio=x_ratio,
                    y_ratio_from_top=y_ratio,
                    clicks=1,
                )
            except Exception as e:
                entry["click"] = {"error": str(e)}

            await asyncio.sleep(sleep_sec)

            if args.cursor_shot:
                cursor_path = base / f"calib_cursor_{idx}_{int(time.time() * 1000)}.png"
                proc = subprocess.run(
                    ["screencapture", "-C", "-x", str(cursor_path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                entry["cursorShot"] = {
                    "ok": proc.returncode == 0,
                    "path": str(cursor_path),
                    "stderr": (proc.stderr or "").strip(),
                }

            # Paste (ASCII text) instead of typing to avoid IME interference.
            try:
                await ui.hotkey("v", modifiers=["command down"])
            except Exception as e:
                entry.setdefault("warnings", []).append(f"paste_failed: {e}")

            await asyncio.sleep(sleep_sec)

            try:
                entry["focus"] = await ui.get_focused_ui_element_info("酷狗音乐")
            except Exception as e:
                entry["focus"] = {"ok": False, "error": str(e)}

            # Screenshot after paste.
            tag = f"calib_{idx}_x{x_ratio:.3f}_y{y_ratio:.3f}"
            try:
                entry["capture"] = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=tag)
            except Exception as e:
                entry["capture"] = {"error": str(e), "tag": tag}

            # Cleanup: Cmd+A + Delete (best-effort).
            try:
                await ui.hotkey("a", modifiers=["command down"])
                await asyncio.sleep(0.05)
                await ui.key_code(51)
            except Exception:
                pass

            results.append(entry)
            idx += 1
            await asyncio.sleep(0.08)

    out_path = base / f"calib_results_{int(time.time() * 1000)}.json"
    out_payload = {
        "meta": {
            "runId": os.environ.get("VOICE_ASSISTANT_DEBUG_RUN"),
            "timestampMs": int(time.time() * 1000),
            "text": args.text,
            "x": xs,
            "y": ys,
            "sleepMs": int(args.sleep_ms),
        },
        "results": results,
    }
    out_path.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"WROTE_RESULTS={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
