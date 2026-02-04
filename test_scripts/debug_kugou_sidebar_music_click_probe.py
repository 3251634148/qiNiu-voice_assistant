#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""酷狗左侧栏"音乐"点击精准度验证与搜索入口可达性探测。

目的
- 用脚本验证：当前后端 UI 自动化是否能稳定点击到左侧栏"音乐"入口（避免误点到"视频/MV"页）。
- 在进入音乐主界面后，验证是否能点击到顶部搜索输入框/进入搜索态（出现"取消/历史搜索"）。

输出
- 所有截图与结果 JSON 会落在：~/Documents/VoiceAssistant/ui_debug/<run_id>/

用法
- VOICE_ASSISTANT_DEBUG_RUN=probe_kugou_sidebar_$(date +%s) python test_scripts/debug_kugou_sidebar_music_click_probe.py --cursor-shot

说明
- 该脚本会进行真实点击（高风险）。请确保已授予"辅助功能/屏幕录制"权限，且酷狗窗口可见。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend_py.services.macos_ui_automation import MacOSUIAutomation, OcrBox
from backend_py.services.music_controller import KUGOU_APP_NAMES, KUGOU_OCR_CLICK_KWARGS, KUGOU_ROIS


@dataclass(frozen=True)
class ProbeResult:
    index: int
    dx: float
    dy: float
    click: Dict[str, Any]
    after_capture: Dict[str, Any]
    mode: str
    search_enter: Optional[Dict[str, Any]]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='酷狗左侧栏"音乐"点击精准度探测')
    parser.add_argument(
        '--run-id',
        default='',
        help='如果提供了且 VOICE_ASSISTANT_DEBUG_RUN 为空，则使用该值作为 run_id。',
    )
    parser.add_argument(
        '--cursor-shot',
        action='store_true',
        help='每次点击后，抓一张带光标的全屏截图用于验证。',
    )
    parser.add_argument(
        '--sleep-ms',
        type=int,
        default=320,
        help='点击操作后的等待时间，单位毫秒（默认 320）',
    )
    parser.add_argument(
        '--offsets',
        default='0,0;0,-80;0,-60;0,-40;-60,0;-60,-40;-40,-40;-40,0;-40,-20',
        help='分号分隔的 dx,dy 偏移量列表，单位是图像像素，应用于 OCR 框中心。',
    )
    return parser.parse_args()


def _parse_offsets(value: str) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for part in str(value or '').split(';'):
        p = part.strip()
        if not p:
            continue
        items = [x.strip() for x in p.split(',')]
        if len(items) != 2:
            continue
        try:
            out.append((float(items[0]), float(items[1])))
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

    texts = [_norm_text(getattr(b, 'text', '')) for b in boxes]
    joined = '|'.join([t for t in texts if t])

    if 'mv' in joined or '首唱会' in joined or 'tmelive' in joined:
        return 'mv_page'
    if '推荐' in joined and ('频道' in joined or '歌单' in joined or '歌手' in joined):
        return 'music_main'
    return 'unknown'


def _pick_music_box(boxes: List[OcrBox]) -> Optional[OcrBox]:
    candidates: List[OcrBox] = []
    for b in boxes:
        t = _norm_text(b.text)
        if not t:
            continue
        if '音乐' not in t:
            continue
        candidates.append(b)

    if not candidates:
        return None

    # 优先选置信度高 + 面积大的框
    return max(candidates, key=lambda b: (float(b.confidence), float(b.width) * float(b.height)))


async def _find_sidebar_music_anchor(ui: MacOSUIAutomation, cap: Dict[str, Any]) -> Tuple[OcrBox, Dict[str, Any]]:
    path = str(cap.get('screenshotPath') or '')
    if not path:
        raise RuntimeError('missing screenshotPath')

    # 使用与生产环境相同的 OCR 配置，但应用侧边栏 ROI。
    boxes = await ui.ocr_screenshot_advanced(
        path,
        roi=KUGOU_ROIS['sidebar'],
        scale=float(KUGOU_OCR_CLICK_KWARGS.get('ocr_scale') or 1.0),
        grayscale=bool(KUGOU_OCR_CLICK_KWARGS.get('ocr_grayscale')),  # type: ignore[arg-type]
        accurate=bool(KUGOU_OCR_CLICK_KWARGS.get('ocr_accurate')),  # type: ignore[arg-type]
        language_correction=bool(KUGOU_OCR_CLICK_KWARGS.get('ocr_language_correction')),  # type: ignore[arg-type]
        languages=KUGOU_OCR_CLICK_KWARGS.get('ocr_languages'),
        custom_words=KUGOU_OCR_CLICK_KWARGS.get('ocr_custom_words'),
    )

    picked = _pick_music_box(boxes)
    if picked is None:
        preview = [getattr(b, 'text', '') for b in sorted(boxes, key=lambda x: x.confidence, reverse=True)[:20]]
        raise RuntimeError(f'未在侧边栏 ROI 内找到"音乐"文本（preview={preview}）')

    meta = {
        'picked': {
            'text': picked.text,
            'confidence': picked.confidence,
            'imageBox': {'x': picked.x, 'y': picked.y, 'width': picked.width, 'height': picked.height},
        },
        'roi': KUGOU_ROIS['sidebar'],
        'boxesTotal': len(boxes),
        'preview': [getattr(b, 'text', '') for b in sorted(boxes, key=lambda x: x.confidence, reverse=True)[:12]],
    }
    return (picked, meta)


async def _try_enter_search(ui: MacOSUIAutomation, cap: Dict[str, Any]) -> Dict[str, Any]:
    """Try to click inside search bar ROI and verify search view by OCR."""

    iw = float(((cap.get('imageSize') or {}).get('width')) or 0.0)
    ih = float(((cap.get('imageSize') or {}).get('height')) or 0.0)
    if iw <= 1 or ih <= 1:
        raise RuntimeError('missing imageSize')

    # 搜索栏 ROI 内的确定性点击点（与 music_controller 的 fallback 逻辑一致）。
    x0, y0, w, h = KUGOU_ROIS['search_bar']
    x_norm = float(x0) + float(w) * 0.78
    y_norm = float(y0) + float(h) * 0.55
    image_x = float(iw) * x_norm
    image_y = float(ih) * y_norm

    sx, sy = ui._to_screen_point_from_window_image_point(  # pylint: disable=protected-access
        image_x,
        image_y,
        window_bounds=cap.get('windowBounds') or {},
        image_size=cap.get('imageSize') or {},
    )
    await ui.click_at(float(sx), float(sy), clicks=2)
    await asyncio.sleep(0.35)

    cap2 = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag='probe_after_enter_search')
    path2 = str(cap2.get('screenshotPath') or '')

    roi_top = KUGOU_ROIS['top_search']
    boxes = await ui.ocr_screenshot_advanced(
        path2,
        roi=roi_top,
        scale=2.4,
        grayscale=True,
        accurate=False,
        custom_words=['取消', '历史搜索', '搜索'],
    )
    texts = [_norm_text(getattr(b, 'text', '')) for b in boxes]
    ok = ('取消' in '|'.join(texts)) or ('历史搜索' in '|'.join(texts))

    return {
        'click': {'imagePoint': {'x': image_x, 'y': image_y}, 'screenPoint': {'x': sx, 'y': sy}},
        'afterCapture': cap2,
        'verify': {'ok': bool(ok), 'texts': [getattr(b, 'text', '') for b in boxes[:20]], 'roi': roi_top},
    }


async def main() -> int:
    args = _parse_args()

    if not os.environ.get('VOICE_ASSISTANT_DEBUG_RUN') and args.run_id:
        os.environ['VOICE_ASSISTANT_DEBUG_RUN'] = str(args.run_id).strip()

    if not os.environ.get('VOICE_ASSISTANT_DEBUG_RUN'):
        os.environ['VOICE_ASSISTANT_DEBUG_RUN'] = f'probe_kugou_sidebar_{int(time.time())}'

    sleep_sec = max(0.02, float(args.sleep_ms) / 1000.0)

    ui = MacOSUIAutomation()
    await ui.ensure_accessibility_ready()
    await _bring_kugou_front(ui)
    await asyncio.sleep(0.6)

    base = ui._debug_dir()  # pylint: disable=protected-access
    summary: Dict[str, Any] = {
        'meta': {
            'runId': os.environ.get('VOICE_ASSISTANT_DEBUG_RUN'),
            'timestampMs': _now_ms(),
            'offsets': _parse_offsets(args.offsets),
            'note': 'Probe sidebar_music click accuracy and search entry',
        },
        'baseline': {},
        'anchor': {},
        'cases': [],
    }

    baseline = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag='probe_baseline')
    summary['baseline'] = baseline

    picked, anchor_meta = await _find_sidebar_music_anchor(ui, baseline)
    summary['anchor'] = anchor_meta

    cx, cy = picked.center()

    results: List[ProbeResult] = []
    offsets = _parse_offsets(args.offsets)

    for idx, (dx, dy) in enumerate(offsets):
        await _bring_kugou_front(ui)
        await asyncio.sleep(0.18)

        image_x = float(cx) + float(dx)
        image_y = float(cy) + float(dy)

        sx, sy = ui._to_screen_point_from_window_image_point(  # pylint: disable=protected-access
            image_x,
            image_y,
            window_bounds=baseline.get('windowBounds') or {},
            image_size=baseline.get('imageSize') or {},
        )

        click_info: Dict[str, Any] = {
            'imagePoint': {'x': image_x, 'y': image_y},
            'screenPoint': {'x': sx, 'y': sy},
            'anchorCenter': {'x': float(cx), 'y': float(cy)},
            'offset': {'dx': dx, 'dy': dy},
        }

        ocr_box = {
            'text': picked.text,
            'confidence': picked.confidence,
            'x': picked.x,
            'y': picked.y,
            'width': picked.width,
            'height': picked.height,
        }

        click_debug = await ui.click_at_debug(
            float(sx),
            float(sy),
            clicks=1,
            tag=f"probe_click_debug_{idx}",
            warp_cursor=True,
            settle_sec=0.03,
            window_bounds=baseline.get('windowBounds') or {},
            image_size=baseline.get('imageSize') or {},
            ocr_box=ocr_box,
            cursor_shot=bool(args.cursor_shot),
        )
        click_info['ocrBox'] = ocr_box
        click_info["clickDebug"] = click_debug

        await asyncio.sleep(sleep_sec)

        if args.cursor_shot:
            cursor_path = base / f'probe_cursor_{idx}_{_now_ms()}.png'
            proc = subprocess.run(['screencapture', '-C', '-x', str(cursor_path)], capture_output=True, text=True, check=False)

            compress_meta = None
            if proc.returncode == 0:
                try:
                    compress_meta = await ui.lossless_compress_png(str(cursor_path))
                except Exception as e:
                    compress_meta = {'ok': False, 'error': str(e), 'path': str(cursor_path)}

            click_info['cursorShot'] = {
                'ok': proc.returncode == 0,
                'path': str(cursor_path),
                'stderr': (proc.stderr or '').strip(),
                'pngLosslessCompress': compress_meta,
            }

        after = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=f'probe_after_click_{idx}')
        after_path = str(after.get('screenshotPath') or '')

        mode = await _detect_mode(ui, after_path) if after_path else 'unknown'

        search_enter: Optional[Dict[str, Any]] = None
        if mode == 'music_main':
            try:
                search_enter = await _try_enter_search(ui, after)
            except Exception as e:
                search_enter = {'ok': False, 'error': str(e)}

        results.append(
            ProbeResult(
                index=idx,
                dx=dx,
                dy=dy,
                click=click_info,
                after_capture=after,
                mode=mode,
                search_enter=search_enter,
            )
        )

        summary['cases'].append(
            {
                'index': idx,
                'dx': dx,
                'dy': dy,
                'click': click_info,
                'mode': mode,
                'afterCapture': after,
                'searchEnter': search_enter,
            }
        )

    out_path = base / f'sidebar_music_click_probe_{_now_ms()}.json'
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

    # 输出简洁汇总。
    compact = [
        {
            'idx': r.index,
            'dx': r.dx,
            'dy': r.dy,
            'mode': r.mode,
            'searchOk': bool((r.search_enter or {}).get('verify', {}).get('ok')) if isinstance(r.search_enter, dict) else None,
        }
        for r in results
    ]
    print(json.dumps({'debugDir': str(base), 'results': compact, 'json': str(out_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
