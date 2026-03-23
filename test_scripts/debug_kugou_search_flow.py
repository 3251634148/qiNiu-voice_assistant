# -*- coding: utf-8 -*-
"""KuGou end-to-end search & play flow debug.

目标：验证“从主页进入搜索 → 输入关键词 → 播放第一首”是否能跑通，并落盘截图证据。

说明：
- 该脚本只用于排障与验证，不修改业务逻辑。
- 统一先回到主页（Cmd+1），再执行搜索与播放（用户要求）。
- 产物落盘到：~/Documents/VoiceAssistant/ui_debug/<run_id>/

使用：
VOICE_ASSISTANT_DEBUG_RUN=<run_id> backend_py/.venv/bin/python test_scripts/debug_kugou_search_flow.py

可选参数：
- --query "周杰伦 告白气球"
- --x 0.72
- --y 0.07
- --y-mode invert|normal

其中：
- y-mode=normal：y 采用当前实现逻辑（y 方向从底到顶）
- y-mode=invert：y 采用反向逻辑（y 方向从顶到下），用于验证坐标系是否相反
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

# 确保以脚本方式运行时能导入项目根目录。
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend_py.services.macos_ui_automation import MacOSUIAutomation
from backend_py.services.music_controller import KUGOU_APP_NAMES, MusicController


def _pbcopy(text: str) -> None:
    subprocess.run(["pbcopy"], input=str(text), text=True, check=False)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Debug KuGou search and play flow")
    parser.add_argument("--query", default="周杰伦 告白气球", help="Search query text")
    parser.add_argument(
        "--mode",
        choices=["service", "legacy"],
        default="service",
        help="service=调用后端OCR工作流；legacy=旧的坐标点位脚本",
    )
    parser.add_argument(
        "--warp",
        action="store_true",
        help="service 模式下启用 VOICE_ASSISTANT_DEBUG_WARP=1（真实光标先移动再点击）",
    )
    parser.add_argument(
        "--x-enter",
        type=float,
        default=0.72,
        help="x ratio for entering search page (0..1)",
    )
    parser.add_argument(
        "--y-enter",
        type=float,
        default=0.07,
        help="y ratio from top for entering search page (0..1)",
    )
    parser.add_argument(
        "--x-input",
        type=float,
        default=0.28,
        help="x ratio for focusing search input field (0..1)",
    )
    parser.add_argument(
        "--y-input",
        type=float,
        default=0.07,
        help="y ratio from top for focusing search input field (0..1)",
    )
    parser.add_argument(
        "--y-mode",
        choices=["normal", "invert"],
        default="invert",
        help="y mapping mode: normal=use current code mapping; invert=flip y direction",
    )
    parser.add_argument(
        "--x-play",
        type=float,
        default=0.25,
        help="x ratio for clicking the first search result row (0..1)",
    )
    parser.add_argument(
        "--y-play",
        type=float,
        default=0.33,
        help="y ratio from top for clicking the first search result row (0..1)",
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=220,
        help="Delay between steps in ms",
    )
    return parser.parse_args()


def _ensure_run_id() -> str:
    run_id = os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or ""
    if not run_id:
        run_id = f"debug_kugou_search_flow_{int(time.time())}"
        os.environ["VOICE_ASSISTANT_DEBUG_RUN"] = run_id
    return run_id


async def main() -> int:
    args = _parse_args()
    run_id = _ensure_run_id()

    ui = MacOSUIAutomation()
    await ui.ensure_accessibility_ready()

    base = ui._debug_dir()  # pylint: disable=protected-access

    if str(args.mode) == "service":
        if bool(args.warp):
            os.environ["VOICE_ASSISTANT_DEBUG_WARP"] = "1"
        else:
            os.environ.pop("VOICE_ASSISTANT_DEBUG_WARP", None)

        ctl = MusicController()
        result = await ctl.music_ui(
            player="kugou",
            action="search",
            query=str(args.query),
            debug=True,
            dry_run=False,
        )
        out_path = base / f"service_results_{int(time.time() * 1000)}.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"RUN_ID={run_id}")
        print(f"WROTE_RESULTS={out_path}")
        return 0

    # 置前酷狗。
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

    sleep_sec = max(0.02, float(args.sleep_ms) / 1000.0)

    results: Dict[str, Any] = {"runId": run_id, "meta": vars(args), "steps": []}

    # 先回到首页（用户要求）。实际情况酷狗可能停在某首歌的详情页，
    # 所以先尝试一组"退出详情"的快捷键，再强制 Cmd+1。
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
        await asyncio.sleep(0.35)
        try:
            entry["capture"] = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=f"flow_exit_{name}")
        except Exception as e:
            entry["capture"] = {"error": str(e), "tag": f"flow_exit_{name}"}
        results["steps"].append(entry)

    # 再强制回首页（兜底）。
    try:
        await ui.hotkey("1", modifiers=["command down"])
    except Exception:
        pass
    await asyncio.sleep(0.45)

    home = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="flow_home")
    results["steps"].append({"step": "home", "capture": home})

    bounds = home.get("windowBounds") or {}
    bx = float(bounds.get("x") or 0.0)
    by = float(bounds.get("y") or 0.0)
    bw = float(bounds.get("width") or 0.0)
    bh = float(bounds.get("height") or 0.0)

    def _map_point(xr: float, yr: float) -> Dict[str, float]:
        x_ratio = max(0.0, min(1.0, float(xr)))
        y_ratio = max(0.0, min(1.0, float(yr)))
        sx = bx + bw * x_ratio
        if args.y_mode == "normal":
            sy = by + (bh - bh * y_ratio)
        else:
            sy = by + bh * y_ratio
        return {"x": sx, "y": sy}

    # 1) Enter search page by clicking the top search bar region.
    enter_pt = _map_point(args.x_enter, args.y_enter)
    await ui.click_at(enter_pt["x"], enter_pt["y"], clicks=1)
    await asyncio.sleep(0.65)
    entered = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="flow_enter_search")
    results["steps"].append({"step": "enter_search", "click": enter_pt, "capture": entered})

    # 2) Focus the input field on the search page (page may take time to render).
    input_pt = _map_point(args.x_input, args.y_input)
    await ui.click_at(input_pt["x"], input_pt["y"], clicks=1)
    await asyncio.sleep(sleep_sec)

    # 3) Paste query (avoid IME typing issues).
    _pbcopy(str(args.query))
    try:
        await ui.hotkey("v", modifiers=["command down"])
    except Exception as e:
        results["steps"].append({"step": "paste_failed", "error": str(e)})

    await asyncio.sleep(0.5)
    pasted = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="flow_query_pasted")
    results["steps"].append({"step": "query_pasted", "click": input_pt, "capture": pasted})

    # 4) 优先选下拉联想的第一条（通常能得到更稳定的搜索上下文）。
    await ui.key_code(125)  # 下箭头
    await asyncio.sleep(0.12)
    await ui.key_code(36)  # 回车
    await asyncio.sleep(0.9)
    after_suggest = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="flow_after_suggest")
    results["steps"].append({"step": "after_suggest", "capture": after_suggest})

    # 5) 确保搜索结果已显示。
    await ui.key_code(36)  # 回车（兜底）
    await asyncio.sleep(1.1)
    after_search = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="flow_after_search")
    results["steps"].append({"step": "after_search", "capture": after_search})

    # 6) 尝试播放第一条结果。
    # 点击可配置的点位（可调）然后按回车。
    play_pt = _map_point(args.x_play, args.y_play)
    await ui.click_at(play_pt["x"], play_pt["y"], clicks=1)
    await asyncio.sleep(0.12)
    await ui.key_code(36)
    await asyncio.sleep(1.2)
    after_play = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="flow_after_play")
    results["steps"].append({"step": "after_play", "click": play_pt, "capture": after_play})

    out_path = base / f"flow_results_{int(time.time() * 1000)}.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"RUN_ID={run_id}")
    print(f"WROTE_RESULTS={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
