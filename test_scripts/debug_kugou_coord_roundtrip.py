#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""KuGou 坐标映射 round-trip 验证（步骤3）。

目标
- 不修改任何服务端实现（backend_py/services 不动）。
- 复用 `MacOSUIAutomation.screenshot_window` 与 `ocr_screenshot_advanced`，验证：
  1) window screenshot image point（左上原点，像素）↔ CGEvent screen point（左上原点） 的双向映射是否一致
  2) ROI offset 回填、OCR scale 回缩、Vision y 翻转是否一致（通过 ROI OCR vs 全图 OCR 的框坐标对比）

输出
- JSON：~/Documents/VoiceAssistant/ui_debug/<runId>/coord_roundtrip_*.json
- PNG：~/Documents/VoiceAssistant/ui_debug/<runId>/coord_roundtrip_*.png （把 OCR 框与测试点画回整图）

用法
- 建议在酷狗“音乐大页面”可见时运行：
  VOICE_ASSISTANT_DEBUG_RUN=coord_roundtrip_$(date +%s) backend_py/.venv/bin/python test_scripts/debug_kugou_coord_roundtrip.py

注意
- 默认仅做 dry-run（不点击）。如需点击验证，请显式加 --do-click。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend_py.services.macos_ui_automation import MacOSUIAutomation, OcrBox


# 尽量复用现有命名，避免脚本与生产逻辑出现“同名不同义”的分叉。
try:
    from backend_py.services.music_controller import KUGOU_APP_NAMES, KUGOU_OCR_CLICK_KWARGS, KUGOU_ROIS
except Exception:
    KUGOU_APP_NAMES = ["酷狗音乐", "KugouMusic", "Kugou Music"]
    KUGOU_OCR_CLICK_KWARGS = {
        "ocr_scale": 2.4,
        "ocr_grayscale": True,
        "ocr_accurate": False,
        "ocr_language_correction": True,
        "ocr_languages": ["zh-Hans", "zh-Hant", "en-US"],
        "ocr_custom_words": ["音乐", "视频", "我的", "搜索", "取消", "历史搜索"],
    }
    KUGOU_ROIS = {
        "full": (0.0, 0.0, 1.0, 1.0),
        "sidebar": (0.0, 0.18, 0.22, 0.72),
        "top_search": (0.18, 0.00, 0.78, 0.16),
        "search_bar": (0.18, 0.00, 0.78, 0.16),
    }


@dataclass(frozen=True)
class ImagePoint:
    x: float
    y: float


@dataclass(frozen=True)
class ScreenPoint:
    x: float
    y: float


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ensure_run_id(run_id: str) -> str:
    run_id_clean = str(run_id or "").strip()
    if os.environ.get("VOICE_ASSISTANT_DEBUG_RUN"):
        return str(os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or "")
    if run_id_clean:
        os.environ["VOICE_ASSISTANT_DEBUG_RUN"] = run_id_clean
        return run_id_clean
    auto = f"coord_roundtrip_{int(time.time())}"
    os.environ["VOICE_ASSISTANT_DEBUG_RUN"] = auto
    return auto


def _norm_text(s: str) -> str:
    return "".join(str(s or "").split()).replace("\uffff", "").replace("\ufffd", "").lower()


def _pick_box_containing(boxes: Sequence[OcrBox], *, keyword: str) -> Optional[OcrBox]:
    k = _norm_text(keyword)
    candidates: List[OcrBox] = []
    for b in boxes:
        t = _norm_text(getattr(b, "text", ""))
        if not t:
            continue
        if k not in t:
            continue
        candidates.append(b)
    if not candidates:
        return None
    return max(candidates, key=lambda b: (float(b.confidence), float(b.width) * float(b.height)))


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(v)))


def _rect_from_box(b: OcrBox) -> Rect:
    return Rect(x=float(b.x), y=float(b.y), width=float(b.width), height=float(b.height))


def _box_center(b: OcrBox) -> ImagePoint:
    return ImagePoint(x=float(b.x) + float(b.width) / 2.0, y=float(b.y) + float(b.height) / 2.0)


def _png_type_identifier() -> str:
    """Return a PNG type identifier for `CGImageDestinationCreateWithURL`."""

    try:
        from UniformTypeIdentifiers import UTType

        return str(UTType.png().identifier())
    except Exception:
        return "public.png"


def _save_annotated_png(
    *,
    base_path: str,
    out_path: Path,
    rects: Sequence[Rect],
    points: Sequence[ImagePoint],
    labels: Optional[Sequence[str]] = None,
) -> None:
    """在整图上画 OCR 框（红）与测试点（绿十字）。

    说明
    - 坐标系：输入 rect/point 均为 image 坐标（左上原点）。
    - 绘制：CoreGraphics 默认是左下原点，因此绘制时做一次 y 翻转。
    """

    from Foundation import NSURL
    from Quartz import (
        CGBitmapContextCreate,
        CGBitmapContextCreateImage,
        CGContextAddLineToPoint,
        CGContextDrawImage,
        CGContextMoveToPoint,
        CGContextSetLineWidth,
        CGContextSetRGBStrokeColor,
        CGContextStrokePath,
        CGContextStrokeRect,
        CGColorSpaceCreateDeviceRGB,
        CGImageDestinationAddImage,
        CGImageDestinationCreateWithURL,
        CGImageDestinationFinalize,
        CGRectMake,
        kCGBitmapByteOrder32Big,
        kCGImageAlphaPremultipliedLast,
    )

    cg, w, h = MacOSUIAutomation._load_cgimage_sync(str(base_path))  # pylint: disable=protected-access

    cs = CGColorSpaceCreateDeviceRGB()
    bytes_per_row = int(w) * 4
    bitmap_info = int(kCGBitmapByteOrder32Big) | int(kCGImageAlphaPremultipliedLast)
    ctx = CGBitmapContextCreate(None, int(w), int(h), 8, bytes_per_row, cs, bitmap_info)
    if ctx is None:
        raise RuntimeError("无法创建绘制上下文")

    CGContextDrawImage(ctx, CGRectMake(0, 0, float(w), float(h)), cg)

    # 红色矩形框
    CGContextSetLineWidth(ctx, 2.0)
    CGContextSetRGBStrokeColor(ctx, 1.0, 0.1, 0.1, 0.95)
    for r in rects:
        y_bl = float(h) - float(r.y) - float(r.height)
        CGContextStrokeRect(ctx, CGRectMake(float(r.x), y_bl, float(r.width), float(r.height)))

    # 绿色点标记
    CGContextSetRGBStrokeColor(ctx, 0.1, 0.9, 0.2, 0.95)
    for p in points:
        x = float(p.x)
        y_bl = float(h) - float(p.y)
        s = 9.0
        CGContextMoveToPoint(ctx, x - s, y_bl)
        CGContextAddLineToPoint(ctx, x + s, y_bl)
        CGContextMoveToPoint(ctx, x, y_bl - s)
        CGContextAddLineToPoint(ctx, x, y_bl + s)
    CGContextStrokePath(ctx)

    cg_out = None
    try:
        cg_out = CGBitmapContextCreateImage(ctx)
    except Exception:
        cg_out = None

    if cg_out is None:
        try:
            cg_out = ctx.makeImage()
        except Exception:
            cg_out = None

    if cg_out is None:
        raise RuntimeError("无法生成标注后的图片")

    url = NSURL.fileURLWithPath_(str(out_path))
    dest = CGImageDestinationCreateWithURL(url, _png_type_identifier(), 1, None)
    if dest is None:
        raise RuntimeError("无法创建图片输出目标")

    CGImageDestinationAddImage(dest, cg_out, None)
    CGImageDestinationFinalize(dest)


async def _bring_kugou_front(ui: MacOSUIAutomation) -> None:
    subprocess.run(["open", "-a", "酷狗音乐"], capture_output=True, text=True, check=False)
    try:
        await ui.activate_app("酷狗音乐")
    except Exception:
        pass
    try:
        await ui.set_process_frontmost("酷狗音乐")
    except Exception:
        pass


async def _ocr(
    ui: MacOSUIAutomation,
    image_path: str,
    *,
    roi: Optional[Tuple[float, float, float, float]],
    scale: float,
    grayscale: bool,
    accurate: bool,
    language_correction: bool,
    languages: Optional[Sequence[str]],
    custom_words: Optional[Sequence[str]],
) -> List[OcrBox]:
    return await ui.ocr_screenshot_advanced(
        image_path,
        roi=roi,
        scale=float(scale),
        grayscale=bool(grayscale),
        accurate=bool(accurate),
        language_correction=bool(language_correction),
        languages=list(languages) if languages is not None else None,
        custom_words=list(custom_words) if custom_words is not None else None,
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description="KuGou coordinate round-trip verification (step3)")
    parser.add_argument("--run-id", type=str, default="", help="Set VOICE_ASSISTANT_DEBUG_RUN if empty")
    parser.add_argument(
        "--do-click",
        action="store_true",
        help="Perform click_at_debug on the computed target points (high risk). Default: dry-run.",
    )
    parser.add_argument(
        "--keywords",
        type=str,
        default="音乐,视频,我的,搜索",
        help="Comma-separated keywords to locate via OCR.",
    )
    parser.add_argument(
        "--roi-modes",
        type=str,
        default="sidebar,top_search,full",
        help="Comma-separated ROI names to run OCR on (must exist in KUGOU_ROIS).",
    )
    parser.add_argument(
        "--scales",
        type=str,
        default="1.0,2.4,3.2",
        help="Comma-separated OCR scales to compare.",
    )
    args = parser.parse_args()

    run_id = _ensure_run_id(str(args.run_id))
    ui = MacOSUIAutomation()
    await ui.ensure_accessibility_ready()

    await _bring_kugou_front(ui)
    await asyncio.sleep(0.6)

    cap = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="coord_roundtrip_baseline")
    img_path = str(cap.get("screenshotPath") or "")
    if not img_path:
        raise RuntimeError("missing screenshotPath")

    # 基础缩放检查（Retina points vs 像素）
    wb = cap.get("windowBounds") or {}
    isz = cap.get("imageSize") or {}
    wb_w = float(wb.get("width") or 0.0)
    wb_h = float(wb.get("height") or 0.0)
    img_w = float(isz.get("width") or 0.0)
    img_h = float(isz.get("height") or 0.0)
    ratio_x = (wb_w / img_w) if img_w > 1 else 0.0
    ratio_y = (wb_h / img_h) if img_h > 1 else 0.0

    keywords = [k.strip() for k in str(args.keywords or "").split(",") if k.strip()]
    roi_names = [r.strip() for r in str(args.roi_modes or "").split(",") if r.strip()]
    scales: List[float] = []
    for s in str(args.scales or "").split(","):
        ss = s.strip()
        if not ss:
            continue
        try:
            scales.append(float(ss))
        except Exception:
            continue
    if not scales:
        scales = [1.0, 2.4, 3.2]

    # 准备确定性测试点（截图像素坐标）
    img_w_i = float(img_w)
    img_h_i = float(img_h)
    det_points: List[Dict[str, Any]] = []

    def _add_det_point(name: str, x: float, y: float) -> None:
        det_points.append(
            {
                "name": name,
                "imagePoint": {"x": float(x), "y": float(y)},
            }
        )

    if img_w_i > 1 and img_h_i > 1:
        _add_det_point("top_left", 1.0, 1.0)
        _add_det_point("top_right", img_w_i - 2.0, 1.0)
        _add_det_point("bottom_left", 1.0, img_h_i - 2.0)
        _add_det_point("bottom_right", img_w_i - 2.0, img_h_i - 2.0)
        _add_det_point("center", img_w_i * 0.5, img_h_i * 0.5)

        if "search_bar" in KUGOU_ROIS:
            rx, ry, rw, rh = KUGOU_ROIS["search_bar"]
            _add_det_point(
                "search_bar_center",
                img_w_i * (float(rx) + float(rw) * 0.5),
                img_h_i * (float(ry) + float(rh) * 0.5),
            )

    # OCR 对比
    ocr_runs: List[Dict[str, Any]] = []
    anchor_results: List[Dict[str, Any]] = []

    ocr_preset = {
        "scale": float(KUGOU_OCR_CLICK_KWARGS.get("ocr_scale") or 2.4),
        "grayscale": bool(KUGOU_OCR_CLICK_KWARGS.get("ocr_grayscale")),
        "accurate": bool(KUGOU_OCR_CLICK_KWARGS.get("ocr_accurate")),
        "language_correction": bool(KUGOU_OCR_CLICK_KWARGS.get("ocr_language_correction")),
        "languages": KUGOU_OCR_CLICK_KWARGS.get("ocr_languages"),
        "custom_words": KUGOU_OCR_CLICK_KWARGS.get("ocr_custom_words"),
    }

    for roi_name in roi_names:
        roi = None
        if roi_name != "full":
            roi = KUGOU_ROIS.get(roi_name)
        for scale in scales:
            boxes = await _ocr(
                ui,
                img_path,
                roi=roi,
                scale=float(scale),
                grayscale=bool(ocr_preset["grayscale"]),
                accurate=bool(ocr_preset["accurate"]),
                language_correction=bool(ocr_preset["language_correction"]),
                languages=ocr_preset["languages"],
                custom_words=ocr_preset["custom_words"],
            )
            preview = [getattr(b, "text", "") for b in sorted(boxes, key=lambda x: x.confidence, reverse=True)[:18]]
            ocr_runs.append(
                {
                    "roi": roi_name,
                    "roiRect": roi,
                    "scale": float(scale),
                    "boxesTotal": len(boxes),
                    "preview": preview,
                }
            )

            for kw in keywords:
                picked = _pick_box_containing(boxes, keyword=kw)
                if picked is None:
                    anchor_results.append(
                        {
                            "keyword": kw,
                            "roi": roi_name,
                            "scale": float(scale),
                            "ok": False,
                            "reason": "no_match",
                        }
                    )
                    continue

                center = _box_center(picked)
                sx, sy = MacOSUIAutomation._to_screen_point_from_window_image_point(
                    center.x,
                    center.y,
                    window_bounds=wb,
                    image_size=isz,
                )
                ix2, iy2 = MacOSUIAutomation._to_window_image_point_from_screen_point(
                    float(sx),
                    float(sy),
                    window_bounds=wb,
                    image_size=isz,
                )

                rect = _rect_from_box(picked)
                in_box = (
                    float(rect.x) <= float(ix2) <= float(rect.x) + float(rect.width)
                    and float(rect.y) <= float(iy2) <= float(rect.y) + float(rect.height)
                )

                entry: Dict[str, Any] = {
                    "keyword": kw,
                    "roi": roi_name,
                    "roiRect": roi,
                    "scale": float(scale),
                    "ok": True,
                    "matched": {
                        "text": picked.text,
                        "confidence": float(picked.confidence),
                        "box": asdict(rect),
                        "center": asdict(center),
                    },
                    "roundTrip": {
                        "screenPoint": {"x": float(sx), "y": float(sy)},
                        "imagePointBack": {"x": float(ix2), "y": float(iy2)},
                        "deltaToCenter": {"dx": float(ix2) - float(center.x), "dy": float(iy2) - float(center.y)},
                        "inBox": bool(in_box),
                    },
                }

                if args.do_click:
                    click_debug = await ui.click_at_debug(
                        float(sx),
                        float(sy),
                        clicks=1,
                        tag=f"coord_roundtrip_click_{roi_name}_{kw}_{str(scale).replace('.', '_')}",
                        warp_cursor=True,
                        settle_sec=0.03,
                        window_bounds=wb,
                        image_size=isz,
                        ocr_box={
                            "text": picked.text,
                            "x": float(rect.x),
                            "y": float(rect.y),
                            "width": float(rect.width),
                            "height": float(rect.height),
                        },
                    )
                    entry["clickDebug"] = click_debug

                anchor_results.append(entry)

    # 确定性测试点的 round-trip 映射
    det_roundtrip: List[Dict[str, Any]] = []
    for p in det_points:
        ip = p["imagePoint"]
        sx, sy = MacOSUIAutomation._to_screen_point_from_window_image_point(
            float(ip["x"]),
            float(ip["y"]),
            window_bounds=wb,
            image_size=isz,
        )
        ix2, iy2 = MacOSUIAutomation._to_window_image_point_from_screen_point(
            float(sx),
            float(sy),
            window_bounds=wb,
            image_size=isz,
        )
        det_roundtrip.append(
            {
                "name": p["name"],
                "imagePoint": {"x": float(ip["x"]), "y": float(ip["y"])},
                "screenPoint": {"x": float(sx), "y": float(sy)},
                "imagePointBack": {"x": float(ix2), "y": float(iy2)},
                "delta": {"dx": float(ix2) - float(ip["x"]), "dy": float(iy2) - float(ip["y"])},
            }
        )

    # 构建标注图：把所有成功的 OCR 框及其中心点画上去
    rects: List[Rect] = []
    points: List[ImagePoint] = []

    for a in anchor_results:
        if not a.get("ok"):
            continue
        box = (((a.get("matched") or {}).get("box")) or {})
        rects.append(
            Rect(
                x=float(box.get("x") or 0.0),
                y=float(box.get("y") or 0.0),
                width=float(box.get("width") or 0.0),
                height=float(box.get("height") or 0.0),
            )
        )
        c = (((a.get("matched") or {}).get("center")) or {})
        points.append(ImagePoint(x=float(c.get("x") or 0.0), y=float(c.get("y") or 0.0)))

    for d in det_roundtrip:
        ip = d.get("imagePoint") or {}
        points.append(ImagePoint(x=float(ip.get("x") or 0.0), y=float(ip.get("y") or 0.0)))

    debug_dir = ui._debug_dir()  # pylint: disable=protected-access
    out_json = debug_dir / f"coord_roundtrip_{_now_ms()}.json"
    out_png = debug_dir / f"coord_roundtrip_{_now_ms()}.png"

    payload: Dict[str, Any] = {
        "meta": {
            "runId": run_id,
            "timestampMs": _now_ms(),
            "frontmost": None,
        },
        "capture": cap,
        "scale": {
            "windowBoundsToImageRatio": {"x": ratio_x, "y": ratio_y},
            "note": "ratio=windowBounds(points)/imageSize(pixels); Retina 常见约 0.5",
        },
        "config": {
            "keywords": keywords,
            "roiModes": roi_names,
            "scales": scales,
            "doClick": bool(args.do_click),
            "ocrPreset": ocr_preset,
        },
        "deterministicRoundTrip": det_roundtrip,
        "ocrRuns": ocr_runs,
        "anchors": anchor_results,
        "artifacts": {
            "annotatedPng": str(out_png),
            "json": str(out_json),
        },
    }

    try:
        payload["meta"]["frontmost"] = await ui.get_frontmost_process_name()
    except Exception as e:
        payload["meta"]["frontmost"] = {"ok": False, "error": str(e)}

    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        _save_annotated_png(
            base_path=img_path,
            out_path=out_png,
            rects=rects,
            points=points,
        )
    except Exception as e:
        payload["artifacts"]["annotateError"] = str(e)
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"debugDir": str(debug_dir), "json": str(out_json), "png": str(out_png)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
