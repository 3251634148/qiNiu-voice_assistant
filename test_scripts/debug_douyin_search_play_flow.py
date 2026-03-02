#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""抖音（macOS）OCR-first 搜索并播放调试脚本。

目的
- 复现并验证：打开抖音 -> 归一化窗口 -> 搜索 query -> 切到“视频” -> 点击最匹配标题播放。
- 所有调试产物落盘到：~/Documents/VoiceAssistant/ui_debug/<VOICE_ASSISTANT_DEBUG_RUN>/

用法
- dry-run（不点击不键入，仅落盘证据）：
  VOICE_ASSISTANT_DEBUG_RUN=douyin_dry_$(date +%s) backend_py/.venv/bin/python test_scripts/debug_douyin_search_play_flow.py --dry-run --query "甄嬛传 解析"

- 真实执行（高风险）：
  VOICE_ASSISTANT_DEBUG_RUN=douyin_run_$(date +%s) backend_py/.venv/bin/python test_scripts/debug_douyin_search_play_flow.py --query "甄嬛传 解析"

说明
- 若 ROI 不适配你的版本，可通过环境变量覆盖：
  - DOUYIN_SIDEBAR_ROI="x,y,w,h"
  - DOUYIN_TOP_SEARCH_ROI="x,y,w,h"
  - DOUYIN_RESULTS_TABS_ROI="x,y,w,h"
  - DOUYIN_RESULTS_CONTENT_ROI="x,y,w,h"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend_py.services.douyin_controller import DouyinController
from backend_py.services.ui_workflow_utils import get_ui_debug_dir


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Debug Douyin search and play flow")
    parser.add_argument("--query", default="甄嬛传 解析", help="Search query")
    parser.add_argument("--dry-run", action="store_true", help="Do not click/type, only dump evidence")
    parser.add_argument("--debug", action="store_true", help="Return debug payload")
    return parser.parse_args()


async def main() -> int:
    args = _parse_args()

    if not os.environ.get("VOICE_ASSISTANT_DEBUG_RUN"):
        os.environ["VOICE_ASSISTANT_DEBUG_RUN"] = f"debug_douyin_{int(time.time())}"

    ctl = DouyinController()
    result = await ctl.search_and_play(query=str(args.query), debug=bool(args.debug), dry_run=bool(args.dry_run))

    out_dir = get_ui_debug_dir()
    out_path = out_dir / f"douyin_flow_result_{int(time.time() * 1000)}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"debugDir": str(out_dir), "result": result, "json": str(out_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
