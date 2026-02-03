# -*- coding: utf-8 -*-
"""End-to-end test: KuGou `music_ui(search)` with OCR-first + coordinate fallback.

目标：
- 从服务侧直接调用 `MusicController.music_ui(player="kugou", action="search")`
- 多轮运行并输出每轮的 debug 关键信息（mode / uiChange / 产物 runId）

说明：
- 该脚本会触发真实 UI 自动化操作（需要 macOS 辅助功能与屏幕录制权限）。
- 调试产物会落到：~/Documents/VoiceAssistant/ui_debug/<VOICE_ASSISTANT_DEBUG_RUN>/

用法：
backend_py/.venv/bin/python test_scripts/debug_kugou_search_service_ocr_fallback.py --query "周杰伦 告白气球" --runs 3
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path

# Ensure project root is importable when running as a script.
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend_py.services.music_controller import MusicController  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Debug service KuGou search: OCR-first + fallback")
    parser.add_argument("--query", type=str, default="周杰伦 告白气球", help="Search query")
    parser.add_argument("--runs", type=int, default=2, help="How many runs")
    parser.add_argument("--sleep-sec", type=float, default=0.8, help="Sleep between runs")
    parser.add_argument("--dry-run", action="store_true", help="Only simulate (no clicks)")
    return parser.parse_args()


async def main() -> int:
    args = _parse_args()

    controller = MusicController()

    ok = 0
    failed = 0
    details = []

    ts = int(time.time())
    for idx in range(max(1, int(args.runs))):
        run_id = f"kugou_service_ocr_fallback_{ts}_{idx}"
        os.environ["VOICE_ASSISTANT_DEBUG_RUN"] = run_id

        try:
            res = await controller.music_ui(
                player="kugou",
                action="search",
                query=str(args.query),
                debug=True,
                dry_run=bool(args.dry_run),
            )
            dbg = res.get("debug") if isinstance(res, dict) else None
            mode = (dbg or {}).get("mode") if isinstance(dbg, dict) else None
            ui_change = (dbg or {}).get("uiChange") if isinstance(dbg, dict) else None

            ok += 1
            details.append({"runId": run_id, "ok": True, "mode": mode, "uiChange": ui_change})

            print("OK", {"runId": run_id, "mode": mode, "uiChange": ui_change, "message": res.get("message")})
        except Exception as e:
            failed += 1
            details.append({"runId": run_id, "ok": False, "error": str(e)})
            print("FAILED", {"runId": run_id, "error": str(e)})

        await asyncio.sleep(max(0.0, float(args.sleep_sec)))

    print("SUMMARY", {"query": args.query, "runs": args.runs, "ok": ok, "failed": failed, "details": details})
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
