#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""企业微信（macOS）OCR-first 搜索联系人并发送消息调试脚本。

目的
- 复现并验证：打开企业微信 -> 归一化窗口 ->（优先会话列表直达，否则 Cmd+F 聚焦顶部搜索框并回车搜索）-> 进入会话 -> 标题区校验 -> 发送消息 -> 恢复草稿。
- 所有调试产物落盘到：~/Documents/VoiceAssistant/ui_debug/<VOICE_ASSISTANT_DEBUG_RUN>/

用法
- dry-run（不点击不键入，仅落盘证据）：
  VOICE_ASSISTANT_DEBUG_RUN=wecom_dry_$(date +%s) backend_py/.venv/bin/python test_scripts/debug_wecom_search_send_flow.py --dry-run --contact "罗晨曦" --message "你好"

- 真实执行（高风险，会发送消息）：
  VOICE_ASSISTANT_DEBUG_RUN=wecom_run_$(date +%s) backend_py/.venv/bin/python test_scripts/debug_wecom_search_send_flow.py --contact "罗晨曦" --message "你好"

说明
- 若 ROI 不适配你的版本，可通过环境变量覆盖：
  - WECOM_LEFT_NAV_ROI="x,y,w,h"
  - WECOM_CHAT_LIST_ROI="x,y,w,h"
  - WECOM_CHAT_HEADER_ROI="x,y,w,h"
  - WECOM_SEARCH_POPUP_ROI="x,y,w,h"
  - WECOM_GLOBAL_TABS_ROI="x,y,w,h"
  - WECOM_RESULTS_LIST_ROI="x,y,w,h"
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

from backend_py.services.ui_workflow_utils import get_ui_debug_dir
from backend_py.services.wecom_ui_controller import WeComUIController


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Debug WeCom UI search+send flow")
    parser.add_argument("--contact", required=True, help="Contact name")
    parser.add_argument("--message", required=True, help="Message to send")
    parser.add_argument("--dry-run", action="store_true", help="Do not click/type")
    parser.add_argument(
        "--confirm-send",
        action="store_true",
        help="Allow real sending (otherwise script forces dry-run)",
    )
    parser.add_argument("--debug", action="store_true", help="Return debug payload")
    return parser.parse_args()


async def main() -> int:
    args = _parse_args()

    if not os.environ.get("VOICE_ASSISTANT_DEBUG_RUN"):
        os.environ["VOICE_ASSISTANT_DEBUG_RUN"] = f"debug_wecom_{int(time.time())}"

    ctl = WeComUIController()

    # 默认不允许真实发送，避免误发：只有显式 --confirm-send 才会执行 Enter 发送。
    effective_dry_run = bool(args.dry_run) or (bool(getattr(args, "confirm_send", False)) is False)

    result = await ctl.search_contact_and_send(
        contact_name=str(args.contact),
        message=str(args.message),
        debug=bool(args.debug),
        dry_run=effective_dry_run,
    )

    out_dir = get_ui_debug_dir()
    out_path = out_dir / f"wecom_flow_result_{int(time.time() * 1000)}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"debugDir": str(out_dir), "result": result, "json": str(out_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
