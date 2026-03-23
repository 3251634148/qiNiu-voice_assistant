#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""酷狗左侧栏"音乐"点击校准脚本。

目的
- 用网格扫描左侧栏区域：逐点点击 -> 截图 -> OCR 判定页面是否进入音乐主界面。
- 为"点击音乐入口误入视频/MV"问题提供可复现证据，并输出可用的确定性点位。

输出
- 所有截图与结果 JSON 会落在：~/Documents/VoiceAssistant/ui_debug/<run_id>/

用法
- VOICE_ASSISTANT_DEBUG_RUN=calib_kugou_sidebar_$(date +%s) backend_py/.venv/bin/python test_scripts/debug_kugou_sidebar_music_calib.py --cursor-shot

说明
- 该脚本会进行真实点击（高风险）。请确保已授予"辅助功能/屏幕录制"权限且酷狗窗口可见。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend_py.services.macos_ui_automation import MacOSUIAutomation
from backend_py.services.music_controller import KUGOU_APP_NAMES


def _now_ms() -> int:
    return int(time.time() * 1000)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='酷狗左侧栏"音乐"点击校准')
    parser.add_argument(
        '--run-id',
        default='',
        help='如果提供了且 VOICE_ASSISTANT_DEBUG_RUN 为空，则使用该值作为 run_id。',
    )
    parser.add_argument(
        '--sleep-ms',
        type=int,
        default=320,
        help='点击后的等待时间，单位毫秒（默认 320）',
    )
    parser.add_argument(
        '--cursor-shot',
        action='store_true',
        help='每次点击后，抓一张带光标的全屏截图用于验证。',
    )
    parser.add_argument(
        '--x',
        default='0.03,0.05,0.07,0.09,0.11',
        help='逗号分隔的 x_ratio 网格值（0..1），默认扫描左侧栏区域。',
    )
    parser.add_argument(
        '--y',
        default='0.28,0.31,0.34,0.37,0.40,0.43,0.46,0.49,0.52',
        help='逗号分隔的 y_ratio_from_top 网格值（0..1），默认扫描侧边栏图标区域。',
    )
    return parser.parse_args()


def _parse_grid(values: str) -> List[float]:
    out: List[float] = []
    for part in str(values or '').split(','):
        p = part.strip()
        if not p:
            continue
        try:
            out.append(float(p))
        except Exception:
            continue
    return out


def _norm_text(s: str) -> str:
    return ''.join(str(s or '').split()).lower()


async def _bring_kugou_front(ui: MacOSUIAutomation) -> None:
    subprocess.run(['open', '-a', '酷狗音乐'], capture_output=True, text=True, check=False)
    try:
        await ui.activate_app('酷狗音乐')
    except Exception:
        pass
    try:
        await ui.set_process_frontmost('酷狗音乐')
    except Exception:
        pass


async def _detect_mode(ui: MacOSUIAutomation, screenshot_path: str) -> str:
    """通过顶部标签栏 OCR 判断当前页面模式（尽力判断）。"""

    roi_top = (0.12, 0.00, 0.88, 0.22)
    boxes = await ui.ocr_screenshot_advanced(
        screenshot_path,
        roi=roi_top,
        scale=2.4,
        grayscale=True,
        accurate=False,
        custom_words=['MV', '推荐', '频道', '歌单', '歌手', '首唱会', '影视', 'TME LIVE'],
    )

    joined = '|'.join([_norm_text(getattr(b, 'text', '')) for b in boxes])
    if 'mv' in joined or '首唱会' in joined or 'tmelive' in joined:
        return 'mv_page'
    if '推荐' in joined and ('频道' in joined or '歌单' in joined or '歌手' in joined):
        return 'music_main'
    return 'unknown'


async def main() -> int:
    args = _parse_args()

    if not os.environ.get('VOICE_ASSISTANT_DEBUG_RUN') and args.run_id:
        os.environ['VOICE_ASSISTANT_DEBUG_RUN'] = str(args.run_id).strip()

    if not os.environ.get('VOICE_ASSISTANT_DEBUG_RUN'):
        os.environ['VOICE_ASSISTANT_DEBUG_RUN'] = f'calib_kugou_sidebar_{int(time.time())}'

    ui = MacOSUIAutomation()
    await ui.ensure_accessibility_ready()
    await _bring_kugou_front(ui)
    await asyncio.sleep(0.6)

    base = ui._debug_dir()  # pylint: disable=protected-access

    xs = _parse_grid(args.x)
    ys = _parse_grid(args.y)
    sleep_sec = max(0.02, float(args.sleep_ms) / 1000.0)

    results: List[Dict[str, Any]] = []

    start_cap = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag='sidebar_calib_start')
    results.append({'step': 'start', 'capture': start_cap})

    idx = 0
    for y_ratio in ys:
        for x_ratio in xs:
            await _bring_kugou_front(ui)
            await asyncio.sleep(0.12)

            entry: Dict[str, Any] = {
                'index': idx,
                'xRatio': x_ratio,
                'yRatioFromTop': y_ratio,
                'click': None,
                'cursorShot': None,
                'afterCapture': None,
                'mode': None,
            }

            try:
                entry['click'] = await ui.click_window_relative(
                    owner_names=KUGOU_APP_NAMES,
                    x_ratio=x_ratio,
                    y_ratio_from_top=y_ratio,
                    clicks=1,
                )
            except Exception as e:
                entry['click'] = {'error': str(e)}

            await asyncio.sleep(sleep_sec)

            if args.cursor_shot:
                cursor_path = base / f'sidebar_calib_cursor_{idx}_{_now_ms()}.png'
                proc = subprocess.run(
                    ['screencapture', '-C', '-x', str(cursor_path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                entry['cursorShot'] = {
                    'ok': proc.returncode == 0,
                    'path': str(cursor_path),
                    'stderr': (proc.stderr or '').strip(),
                }

            try:
                cap = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=f'sidebar_calib_after_{idx}')
                entry['afterCapture'] = cap
                path = str(cap.get('screenshotPath') or '')
                entry['mode'] = await _detect_mode(ui, path) if path else 'unknown'
            except Exception as e:
                entry['afterCapture'] = {'error': str(e)}
                entry['mode'] = 'unknown'

            results.append(entry)
            idx += 1

    out_path = base / f'sidebar_music_calib_{_now_ms()}.json'
    out_path.write_text(json.dumps({'results': results}, ensure_ascii=False, indent=2), encoding='utf-8')

    # 输出一份简洁的汇总，方便快速查看命中结果。
    hits = [
        {
            'index': r.get('index'),
            'x': r.get('xRatio'),
            'y': r.get('yRatioFromTop'),
            'mode': r.get('mode'),
        }
        for r in results
        if isinstance(r, dict) and r.get('mode') in {'music_main', 'mv_page'}
    ]
    print(json.dumps({'debugDir': str(base), 'hits': hits, 'json': str(out_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
