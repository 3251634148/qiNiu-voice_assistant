#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""企微截图坐标 vs macOS 坐标系校准脚本。

目标
- 给出“同一个物理点”在不同坐标口径下的数值：
  - Quartz event-space（CGEvent）：左上原点，y 向下
  - AppKit global（NSEvent.mouseLocation）：左下原点，y 向上
  - 单屏局部坐标（AppKit screen frame）：左下原点，y 向上（用于对齐企微截图读数）
- 通过同点双 API 采样推断 globalMaxY：globalMaxY = appkitY + quartzY
- 可选：将鼠标 warp 到“企微坐标（单屏左下原点）”推算出的 event-space 点位，以人工复核。

用法
- 仅报告当前光标在各口径下的坐标（不移动鼠标）：
  VOICE_ASSISTANT_DEBUG_RUN=wecom_calib_$(date +%s) backend_py/.venv/bin/python test_scripts/debug_wecom_coord_calibration.py

- 将鼠标移动到企微读数的点位（高风险：会移动真实光标）：
  VOICE_ASSISTANT_DEBUG_RUN=wecom_calib_$(date +%s) backend_py/.venv/bin/python test_scripts/debug_wecom_coord_calibration.py \
    --warp --screen-index 0 --wecom "361,636"

说明
- 本脚本不执行点击，仅移动光标（可选）。
- 产物会落盘到：~/Documents/VoiceAssistant/ui_debug/<runId>/wecom_coord_calib_*.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend_py.services.macos_ui_automation import MacOSUIAutomation


def _now_ms() -> int:
    return int(time.time() * 1000)


def _debug_dir() -> Path:
    base = Path.home() / 'Documents' / 'VoiceAssistant' / 'ui_debug'
    run_id = str(os.environ.get('VOICE_ASSISTANT_DEBUG_RUN') or '').strip()
    if run_id:
        safe = re.sub(r'[^0-9a-zA-Z_.-]+', '_', run_id).strip('_')
        safe = safe[:120] if safe else ''
        if safe:
            base = base / safe
    base.mkdir(parents=True, exist_ok=True)
    return base


def _parse_point(value: str) -> Tuple[float, float]:
    items = [x.strip() for x in str(value or '').split(',')]
    if len(items) != 2:
        raise ValueError('invalid point, expected "x,y"')
    return (float(items[0]), float(items[1]))


def _as_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None


def _infer_global_max_y_sync() -> Optional[dict[str, Any]]:
    try:
        from AppKit import NSEvent
        from Quartz import CGEventCreate, CGEventGetLocation
    except Exception:
        return None

    try:
        appkit_pt = NSEvent.mouseLocation()
        quartz_pt = CGEventGetLocation(CGEventCreate(None))
        appkit = {'x': float(appkit_pt.x), 'y': float(appkit_pt.y)}
        quartz = {'x': float(quartz_pt.x), 'y': float(quartz_pt.y)}
        return {
            'ok': True,
            'appkitMouse': appkit,
            'quartzMouse': quartz,
            'globalMaxY': float(appkit['y']) + float(quartz['y']),
        }
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def _pick_screen_by_index(screens: list[dict[str, Any]], screen_index: int) -> dict[str, Any]:
    """根据 `index` 选取屏幕项。

    注意
    - `index` 可能是 0，所以不能用 `or` 做 fallback。
    """

    for s in screens:
        idx = s.get('index')
        if idx is None:
            continue
        try:
            if int(idx) == int(screen_index):
                return s
        except Exception:
            continue

    raise RuntimeError(f'screenIndex={screen_index} not found, screens={screens}')


def _to_event_from_wecom_local(
    *,
    wecom_x: float,
    wecom_y: float,
    screen_frame: dict[str, Any],
    global_max_y: float,
) -> dict[str, float]:
    """Convert single-screen bottom-left coords -> Quartz event-space coords."""

    fx = float(screen_frame.get('x') or 0.0)
    fy = float(screen_frame.get('y') or 0.0)

    appkit_x = fx + float(wecom_x)
    appkit_y = fy + float(wecom_y)

    event_x = float(appkit_x)
    event_y = float(global_max_y) - float(appkit_y)
    return {'x': float(event_x), 'y': float(event_y)}


def _to_wecom_local_from_event(
    *,
    event_x: float,
    event_y: float,
    screen_frame: dict[str, Any],
    global_max_y: float,
) -> dict[str, float]:
    """将 Quartz event-space 坐标转换为单屏左下角原点坐标。"""

    fx = float(screen_frame.get('x') or 0.0)
    fy = float(screen_frame.get('y') or 0.0)

    appkit_x = float(event_x)
    appkit_y = float(global_max_y) - float(event_y)

    return {'x': float(appkit_x) - fx, 'y': float(appkit_y) - fy}


async def _cursor_shot(tag: str) -> Optional[str]:
    out_path = _debug_dir() / f'{tag}_{_now_ms()}.png'

    def _run() -> bool:
        proc = subprocess.run(
            ['screencapture', '-x', '-C', str(out_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0

    ok = await asyncio.to_thread(_run)
    return str(out_path) if ok else None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='WeCom screenshot coordinate calibration (macOS)')
    parser.add_argument('--warp', action='store_true', help='Move (warp) the real cursor to the computed point.')
    parser.add_argument('--screen-index', type=int, default=0, help='Target screen index from NSScreen.screens().')
    parser.add_argument('--wecom', default='', help='WeCom coordinate as "x,y" (single-screen bottom-left).')
    parser.add_argument('--cursor-shot', action='store_true', help='Capture a full-screen screenshot with cursor.')
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    ui = MacOSUIAutomation()

    snapshot = ui._get_screens_snapshot_sync()  # pylint: disable=protected-access
    screens = snapshot.get('screens') or []
    if not isinstance(screens, list) or not screens:
        raise RuntimeError(f'no screens found: {snapshot}')

    global_by_screens = _as_float(snapshot.get('globalMaxYByScreens'))
    global_inferred = _as_float(snapshot.get('globalMaxYInferred'))

    inferred_live = _infer_global_max_y_sync()
    global_inferred_live = _as_float((inferred_live or {}).get('globalMaxY'))

    global_used = global_inferred_live or global_inferred or global_by_screens
    if global_used is None:
        global_used = float(ui._get_global_desktop_max_y())  # pylint: disable=protected-access

    screen = _pick_screen_by_index(screens, int(args.screen_index))
    frame = screen.get('frame') or {}

    before_event = await ui.get_mouse_position()
    before_appkit = (inferred_live or {}).get('appkitMouse')

    warp_target = None
    if str(args.wecom or '').strip():
        wx, wy = _parse_point(args.wecom)
        warp_target = {
            'wecomLocal': {'x': float(wx), 'y': float(wy)},
            'eventByUsed': _to_event_from_wecom_local(wecom_x=wx, wecom_y=wy, screen_frame=frame, global_max_y=global_used),
        }

    cursor_shots: Dict[str, Any] = {}
    if args.cursor_shot:
        cursor_shots['before'] = await _cursor_shot('wecom_cursor_before')

    if args.warp:
        if warp_target is None:
            raise RuntimeError('--warp requires --wecom "x,y"')

        pt = warp_target['eventByUsed']
        await ui.warp_mouse(float(pt['x']), float(pt['y']))
        await asyncio.sleep(0.08)

        if args.cursor_shot:
            cursor_shots['afterWarp'] = await _cursor_shot('wecom_cursor_after_warp')

    after_event = await ui.get_mouse_position()
    inferred_after = _infer_global_max_y_sync()
    global_inferred_after = _as_float((inferred_after or {}).get('globalMaxY'))

    wecom_local_used = _to_wecom_local_from_event(
        event_x=float(after_event['x']),
        event_y=float(after_event['y']),
        screen_frame=frame,
        global_max_y=float(global_used),
    )

    wecom_local_by_screens = None
    if global_by_screens is not None:
        wecom_local_by_screens = _to_wecom_local_from_event(
            event_x=float(after_event['x']),
            event_y=float(after_event['y']),
            screen_frame=frame,
            global_max_y=float(global_by_screens),
        )

    wecom_local_by_inferred = None
    if global_inferred_after is not None:
        wecom_local_by_inferred = _to_wecom_local_from_event(
            event_x=float(after_event['x']),
            event_y=float(after_event['y']),
            screen_frame=frame,
            global_max_y=float(global_inferred_after),
        )

    payload: Dict[str, Any] = {
        'timestampMs': _now_ms(),
        'screenIndex': int(args.screen_index),
        'screenFrame': frame,
        'snapshot': {
            'globalMaxYByScreens': global_by_screens,
            'globalMaxYInferred': global_inferred,
            'inferMeta': snapshot.get('inferMeta'),
        },
        'inferredLiveBefore': inferred_live,
        'inferredLiveAfter': inferred_after,
        'globalMaxYUsed': float(global_used),
        'cursorShots': cursor_shots,
        'mouseBefore': {
            'event': before_event,
            'appkit': before_appkit,
        },
        'mouseAfter': {
            'event': after_event,
        },
        'warpTarget': warp_target,
        'wecomLocalFromMouse': {
            'used': wecom_local_used,
            'byScreens': wecom_local_by_screens,
            'byInferredAfter': wecom_local_by_inferred,
        },
    }

    out_path = _debug_dir() / f'wecom_coord_calib_{_now_ms()}.json'
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    # 控制台汇总
    print('saved:', out_path)
    print('globalMaxYUsed:', payload['globalMaxYUsed'])
    print('mouse(event):', after_event)
    print('wecomLocal(used):', wecom_local_used)
    if wecom_local_by_screens is not None:
        print('wecomLocal(byScreens):', wecom_local_by_screens)
    if wecom_local_by_inferred is not None:
        print('wecomLocal(byInferredAfter):', wecom_local_by_inferred)


if __name__ == '__main__':
    asyncio.run(main())
