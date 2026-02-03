#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Vision OCR 评测脚本（用于酷狗 UI 自动化可行性验证）。

目标
- 复用现有的窗口定位与截图能力（`MacOSUIAutomation.screenshot_window`）
- 使用 macOS Vision `VNRecognizeTextRequest` 跑 OCR
- 通过“多 ROI + 多参数 + 多预处理”尝试，评估 OCR 对酷狗界面关键字的可识别性

输出
- 结果会写入 `~/Documents/VoiceAssistant/ui_debug/<runId>/ocr_eval_report_*.json`
- 同时会将本次截图/ROI 裁剪图落盘，便于人工对照

使用示例
- 现场抓取酷狗窗口并评测：
  VOICE_ASSISTANT_DEBUG_RUN=ocr_eval_$(date +%s) backend_py/.venv/bin/python test_scripts/debug_ocr_vision_kugou.py --capture

- 对已有截图离线评测：
  backend_py/.venv/bin/python test_scripts/debug_ocr_vision_kugou.py --image /path/to.png

注意
- 该脚本是“排障/评测工具”，不修改任何业务逻辑。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

# Ensure project root is on sys.path so `backend_py` can be imported when running from `test_scripts/`.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend_py.services.macos_ui_automation import MacOSUIAutomation


KUGOU_APP_NAMES = ["酷狗音乐", "KugouMusic", "Kugou Music"]


@dataclass
class OcrBox:
    text: str
    confidence: float
    # image coords (origin top-left)
    x: float
    y: float
    width: float
    height: float


def _debug_dir() -> Path:
    base = Path.home() / "Documents" / "VoiceAssistant" / "ui_debug"
    run_id = str(os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or "").strip()
    out = base / run_id if run_id else base
    out.mkdir(parents=True, exist_ok=True)
    return out


def _now_ms() -> int:
    return int(time.time() * 1000)


def _import_vision() -> None:
    """Ensure Vision/Quartz/Cocoa imports work.

    Note: This script intentionally does NOT require `pyobjc-framework-CoreImage`.
    Some environments only ship Vision/Quartz/Cocoa, and we can still do useful OCR evaluation.
    """

    try:
        from AppKit import NSImage  # noqa: F401
        from Vision import VNRecognizeTextRequest  # noqa: F401
        from Quartz import CGImageCreateWithImageInRect  # noqa: F401
    except Exception as e:
        raise RuntimeError(
            "Vision OCR 依赖不可用：请确认已在 backend_py/.venv 中安装 PyObjC Vision 相关依赖。\n"
            f"原始错误：{e}"
        )


def _load_cgimage(image_path: str) -> tuple[Any, float, float]:
    """Load image into CGImage and return (cgimage, width, height).

    Use Quartz ImageIO instead of `NSImage.CGImageForProposedRect...` to avoid PyObjC signature differences.
    """

    _import_vision()

    from Foundation import NSURL
    from Quartz import CGImageGetHeight, CGImageGetWidth, CGImageSourceCreateImageAtIndex, CGImageSourceCreateWithURL

    url = NSURL.fileURLWithPath_(str(image_path))
    src = CGImageSourceCreateWithURL(url, None)
    if src is None:
        raise RuntimeError(f"无法加载图片：{image_path}")

    cg = CGImageSourceCreateImageAtIndex(src, 0, None)
    if cg is None:
        raise RuntimeError(f"无法获取 CGImage：{image_path}")

    w = float(CGImageGetWidth(cg))
    h = float(CGImageGetHeight(cg))
    return (cg, w, h)


def _crop_cgimage(cgimage: Any, *, w: float, h: float, roi: tuple[float, float, float, float]) -> Any:
    """Crop CGImage by normalized ROI (x, y, width, height) with origin top-left."""

    from Quartz import CGImageCreateWithImageInRect

    rx, ry, rw, rh = roi
    rx = max(0.0, min(1.0, float(rx)))
    ry = max(0.0, min(1.0, float(ry)))
    rw = max(0.0, min(1.0 - rx, float(rw)))
    rh = max(0.0, min(1.0 - ry, float(rh)))

    x = float(rx) * float(w)
    y = float(ry) * float(h)
    ww = float(rw) * float(w)
    hh = float(rh) * float(h)

    # Quartz uses bottom-left origin for CGRect in some contexts; however CGImageCreateWithImageInRect
    # treats rect in image space with origin at top-left for CGImage when used with pixel-based coords.
    # In practice for our PNG captures, using top-left coords works with this cropping.
    rect = ((x, y), (ww, hh))
    cropped = CGImageCreateWithImageInRect(cgimage, rect)
    if cropped is None:
        return cgimage
    return cropped


def _preprocess_cg(
    cgimage: Any,
    *,
    scale: float = 2.0,
    grayscale: bool = True,
) -> Any:
    """Lightweight preprocessing using Quartz/CoreGraphics only.

    - Scale up to help small-font OCR
    - Optional grayscale to reduce color noise

    We do not do contrast/sharpen here because CoreImage isn't guaranteed installed.
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
        return cgimage

    if w0 <= 1 or h0 <= 1:
        return cgimage

    s = max(1.0, float(scale))
    w = int(w0 * s)
    h = int(h0 * s)

    if grayscale:
        cs = CGColorSpaceCreateDeviceGray()
        bpp = 8
        bpc = 8
        bytes_per_row = w
        bitmap_info = 0
    else:
        cs = CGColorSpaceCreateDeviceRGB()
        bpp = 32
        bpc = 8
        bytes_per_row = w * 4
        bitmap_info = 0

    ctx = CGBitmapContextCreate(None, w, h, bpc, bytes_per_row, cs, bitmap_info)
    if ctx is None:
        return cgimage

    CGContextSetInterpolationQuality(ctx, kCGInterpolationHigh)
    CGContextDrawImage(ctx, CGRectMake(0, 0, w, h), cgimage)

    try:
        cg2 = ctx.makeImage()
        return cg2 or cgimage
    except Exception:
        return cgimage


def _vision_ocr(
    cgimage: Any,
    *,
    languages: Optional[list[str]] = None,
    accurate: bool = True,
    language_correction: bool = True,
    min_text_height: Optional[float] = None,
    custom_words: Optional[list[str]] = None,
) -> list[OcrBox]:
    """Run Vision OCR on a CGImage and return boxes in top-left image coordinates."""

    from Vision import VNImageRequestHandler, VNRecognizeTextRequest

    results: list[OcrBox] = []

    def _handler(request: Any, error: Any) -> None:
        if error is not None:
            raise RuntimeError(str(error))

    req = VNRecognizeTextRequest.alloc().initWithCompletionHandler_(_handler)

    # recognitionLevel: 0=fast, 1=accurate
    req.setRecognitionLevel_(1 if accurate else 0)
    req.setUsesLanguageCorrection_(bool(language_correction))

    if languages:
        try:
            req.setRecognitionLanguages_(languages)
        except Exception:
            pass

    if custom_words:
        try:
            req.setCustomWords_(custom_words)
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
    # boundingBox is normalized, origin at lower-left
    for obs in observations:
        candidates = obs.topCandidates_(1)
        if not candidates:
            continue
        best = candidates[0]
        text = str(best.string() or "").strip()
        if not text:
            continue

        conf = float(best.confidence())
        bb = obs.boundingBox()

        # We cannot reliably get width/height from CGImage without additional calls.
        # For evaluation, we keep bbox normalized and also store approximate pixel box
        # if the caller provides image size. We attach normalized in x/y/width/height.
        x = float(bb.origin.x)
        y = float(bb.origin.y)
        ww = float(bb.size.width)
        hh = float(bb.size.height)
        # Convert to top-left normalized
        y_tl = 1.0 - y - hh

        results.append(OcrBox(text=text, confidence=conf, x=x, y=y_tl, width=ww, height=hh))

    return results


def _score_keywords(boxes: list[OcrBox], keywords: Iterable[str], *, min_conf: float = 0.3) -> dict[str, Any]:
    def _norm(s: str) -> str:
        return "".join(str(s or "").split()).replace("\uffff", "").replace("\ufffd", "").lower()

    texts = [b for b in boxes if b.confidence >= float(min_conf)]
    joined = "\n".join([_norm(b.text) for b in texts])

    hits: dict[str, bool] = {}
    for k in keywords:
        hits[str(k)] = _norm(str(k)) in joined

    confs = [b.confidence for b in boxes]
    confs_sorted = sorted(confs, reverse=True)
    return {
        "hits": hits,
        "boxesTotal": len(boxes),
        "confMax": confs_sorted[0] if confs_sorted else 0.0,
        "confP90": confs_sorted[int(len(confs_sorted) * 0.1)] if len(confs_sorted) >= 10 else (confs_sorted[-1] if confs_sorted else 0.0),
        "confAvg": (sum(confs) / len(confs)) if confs else 0.0,
        "preview": [b.text for b in sorted(boxes, key=lambda x: x.confidence, reverse=True)[:18]],
    }


DEFAULT_ANCHORS = [
    "搜索",
    "我的",
    "音乐",
    "我喜欢",
    "创建歌单",
    "单曲",
    "歌单",
]


def _norm_text(value: str) -> str:
    return "".join(str(value or "").split()).replace("\uffff", "").replace("\ufffd", "").lower()


def _match_anchor(box: OcrBox, *, anchor: str, match_mode: str, min_confidence: float) -> bool:
    if float(box.confidence) < float(min_confidence):
        return False

    t = _norm_text(box.text)
    a = _norm_text(anchor)
    if not t or not a:
        return False

    if match_mode == "exact":
        return t == a

    return a in t


def _auto_roi_for_anchor(anchor: str) -> str:
    """Heuristic ROI selection for KuGou UI anchors."""

    a = str(anchor or "")
    if a in {"音乐", "我的", "我喜欢", "创建歌单"}:
        return "sidebar"
    if a in {"搜索", "取消"}:
        return "top_search"
    if a in {"单曲", "歌单", "综合", "视频", "歌手"}:
        return "tabs"
    return "full"


def _roi_box_to_full_box_norm(box: OcrBox, *, roi: tuple[float, float, float, float]) -> OcrBox:
    rx, ry, rw, rh = roi
    return OcrBox(
        text=box.text,
        confidence=box.confidence,
        x=float(rx) + float(box.x) * float(rw),
        y=float(ry) + float(box.y) * float(rh),
        width=float(box.width) * float(rw),
        height=float(box.height) * float(rh),
    )


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(value)))


def _box_norm_to_pixel_rect(box: OcrBox, *, image_w: float, image_h: float) -> dict[str, float]:
    return {
        "x": float(box.x) * float(image_w),
        "y": float(box.y) * float(image_h),
        "w": float(box.width) * float(image_w),
        "h": float(box.height) * float(image_h),
    }


def _png_type_identifier() -> str:
    """Return a PNG type identifier for `CGImageDestinationCreateWithURL`.

    Some macOS/PyObjC environments don't have `UniformTypeIdentifiers`.
    """

    try:
        from UniformTypeIdentifiers import UTType

        return str(UTType.png().identifier())
    except Exception:
        return "public.png"


def _save_annotated_anchor_image(
    *,
    base_cgimage: Any,
    image_w: int,
    image_h: int,
    out_path: Path,
    rects: list[dict[str, float]],
    points: list[dict[str, float]],
) -> None:
    """Annotate base image with rectangles and points.

    Notes:
    - This is a debug tool; we intentionally avoid external deps like Pillow.
    - Coordinates are in image space with origin at top-left.
    """

    from Foundation import NSURL
    from Quartz import (
        CGBitmapContextCreate,
        CGBitmapContextCreateImage,
        CGContextAddLineToPoint,
        CGContextDrawImage,
        CGContextMoveToPoint,
        CGContextScaleCTM,
        CGContextSetLineWidth,
        CGContextSetRGBStrokeColor,
        CGContextStrokePath,
        CGContextStrokeRect,
        CGContextTranslateCTM,
        CGColorSpaceCreateDeviceRGB,
        CGImageDestinationAddImage,
        CGImageDestinationCreateWithURL,
        CGImageDestinationFinalize,
        CGRectMake,
        kCGBitmapByteOrder32Big,
        kCGImageAlphaPremultipliedLast,
    )

    cs = CGColorSpaceCreateDeviceRGB()
    bytes_per_row = int(image_w) * 4
    bitmap_info = int(kCGBitmapByteOrder32Big) | int(kCGImageAlphaPremultipliedLast)
    ctx = CGBitmapContextCreate(
        None,
        int(image_w),
        int(image_h),
        8,
        bytes_per_row,
        cs,
        bitmap_info,
    )
    if ctx is None:
        raise RuntimeError("无法创建绘制上下文")

    # Keep bitmap context in the default CoreGraphics coordinate system (origin bottom-left).
    # Our OCR boxes are in image coordinates with origin top-left, so we convert Y when drawing annotations.

    CGContextDrawImage(ctx, CGRectMake(0, 0, float(image_w), float(image_h)), base_cgimage)

    CGContextSetLineWidth(ctx, 2.0)
    # red rectangles
    CGContextSetRGBStrokeColor(ctx, 1.0, 0.1, 0.1, 0.95)
    for r in rects:
        x = float(r["x"])
        y_tl = float(r["y"])
        w = float(r["w"])
        h = float(r["h"])
        y_bl = float(image_h) - y_tl - h
        CGContextStrokeRect(ctx, CGRectMake(x, y_bl, w, h))

    # green points (crosshair)
    CGContextSetRGBStrokeColor(ctx, 0.1, 0.9, 0.2, 0.95)
    for p in points:
        x = float(p["x"])
        y_tl = float(p["y"])
        y_bl = float(image_h) - y_tl
        s = 9.0
        CGContextMoveToPoint(ctx, x - s, y_bl)
        CGContextAddLineToPoint(ctx, x + s, y_bl)
        CGContextMoveToPoint(ctx, x, y_bl - s)
        CGContextAddLineToPoint(ctx, x, y_bl + s)
    CGContextStrokePath(ctx)

    cg_out = None
    try:
        # Prefer C API to avoid PyObjC differences.
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


async def _capture_kugou(ui: MacOSUIAutomation) -> dict[str, Any]:
    return await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="ocr_eval_capture")


async def _run_anchor_dry_run(
    *,
    cg: Any,
    image_w: float,
    image_h: float,
    capture: Optional[dict[str, Any]],
    rois: dict[str, tuple[float, float, float, float]],
    anchors: list[str],
    anchor_roi: str,
    match_mode: str,
    min_confidence: float,
    offset_x: float,
    offset_y: float,
    offset_unit: str,
    debug_dir: Path,
) -> dict[str, Any]:
    """Dry-run anchor matching: compute theoretical click points and annotate outputs."""

    anchors_clean = [str(a).strip() for a in (anchors or []) if str(a).strip()]
    if not anchors_clean:
        anchors_clean = list(DEFAULT_ANCHORS)

    cfg_candidates = [
        {
            "accurate": False,
            "languageCorrection": True,
            "grayscale": True,
            "scale": 1.0,
            "languages": ["zh-Hans", "zh-Hant", "en-US"],
            "minTextHeight": None,
        },
        {
            "accurate": False,
            "languageCorrection": True,
            "grayscale": True,
            "scale": 2.4,
            "languages": ["zh-Hans", "zh-Hant", "en-US"],
            "minTextHeight": None,
        },
        {
            "accurate": True,
            "languageCorrection": True,
            "grayscale": True,
            "scale": 2.4,
            "languages": ["zh-Hans", "zh-Hant", "en-US"],
            "minTextHeight": None,
        },
        {
            "accurate": False,
            "languageCorrection": False,
            "grayscale": True,
            "scale": 2.4,
            "languages": ["zh-Hans", "zh-Hant", "en-US"],
            "minTextHeight": None,
        },
    ]

    custom_words = sorted({*anchors_clean, "取消", "搜索", "我的", "音乐", "单曲", "歌单", "综合", "视频"})

    def _primary_roi(a: str) -> str:
        if anchor_roi == "auto":
            return _auto_roi_for_anchor(a)
        return anchor_roi

    roi_names: set[str] = {"full"}
    for a in anchors_clean:
        roi_names.add(_primary_roi(a))

    ocr_cache: dict[str, list[dict[str, Any]]] = {}
    for roi_name in sorted(roi_names):
        roi = rois.get(roi_name) or rois.get("full")
        if not roi:
            continue

        crop = _crop_cgimage(cg, w=image_w, h=image_h, roi=roi)
        roi_cases: list[dict[str, Any]] = []

        for cfg in cfg_candidates:
            cg2 = _preprocess_cg(crop, scale=float(cfg["scale"]), grayscale=bool(cfg["grayscale"]))
            boxes = _vision_ocr(
                cg2,
                languages=list(cfg["languages"]),
                accurate=bool(cfg["accurate"]),
                language_correction=bool(cfg["languageCorrection"]),
                min_text_height=cfg["minTextHeight"],
                custom_words=custom_words,
            )
            roi_cases.append({"roi": roi_name, "roiRect": roi, "config": cfg, "boxes": boxes})

        ocr_cache[roi_name] = roi_cases

    matches: list[dict[str, Any]] = []
    rects: list[dict[str, float]] = []
    points: list[dict[str, float]] = []

    for a in anchors_clean:
        primary = _primary_roi(a)
        roi_try = [primary]
        if primary != "full":
            roi_try.append("full")

        best: Optional[dict[str, Any]] = None
        for rn in roi_try:
            roi = rois.get(rn) or rois.get("full")
            if not roi:
                continue

            for case in ocr_cache.get(rn, []):
                boxes = case.get("boxes") or []
                candidates = [b for b in boxes if _match_anchor(b, anchor=a, match_mode=match_mode, min_confidence=min_confidence)]
                if not candidates:
                    continue

                picked = max(candidates, key=lambda b: (float(b.confidence), float(b.width) * float(b.height)))
                cand = {"roi": rn, "roiRect": roi, "config": case.get("config"), "box": picked}
                if best is None:
                    best = cand
                    continue

                bb = best["box"]
                if (float(picked.confidence), float(picked.width) * float(picked.height)) > (
                    float(bb.confidence),
                    float(bb.width) * float(bb.height),
                ):
                    best = cand

        if best is None:
            matches.append({"anchor": a, "ok": False, "reason": "no_match"})
            continue

        roi_name = str(best["roi"])
        roi_rect = best["roiRect"]
        box_roi: OcrBox = best["box"]

        if roi_name == "full":
            box_full = box_roi
        else:
            box_full = _roi_box_to_full_box_norm(box_roi, roi=roi_rect)

        rect_px = _box_norm_to_pixel_rect(box_full, image_w=image_w, image_h=image_h)
        cx = float(rect_px["x"]) + float(rect_px["w"]) / 2.0
        cy = float(rect_px["y"]) + float(rect_px["h"]) / 2.0

        if offset_unit == "box":
            dx = float(offset_x) * float(rect_px["w"])
            dy = float(offset_y) * float(rect_px["h"])
        else:
            dx = float(offset_x)
            dy = float(offset_y)

        click_x = _clamp(cx + dx, 0.0, float(image_w) - 1.0)
        click_y = _clamp(cy + dy, 0.0, float(image_h) - 1.0)

        screen_point = None
        if capture is not None:
            try:
                sx, sy = MacOSUIAutomation._to_screen_point_from_window_image_point(
                    click_x,
                    click_y,
                    window_bounds=capture.get("windowBounds") or {},
                    image_size=capture.get("imageSize") or {},
                )
                screen_point = {"x": sx, "y": sy}
            except Exception:
                screen_point = None

        rects.append(rect_px)
        points.append({"x": click_x, "y": click_y})

        matches.append(
            {
                "anchor": a,
                "ok": True,
                "matched": {
                    "text": box_roi.text,
                    "confidence": box_roi.confidence,
                    "roi": roi_name,
                    "roiRect": {"x": roi_rect[0], "y": roi_rect[1], "w": roi_rect[2], "h": roi_rect[3]},
                    "boxNormTLInRoi": {"x": box_roi.x, "y": box_roi.y, "w": box_roi.width, "h": box_roi.height},
                    "boxNormTL": {"x": box_full.x, "y": box_full.y, "w": box_full.width, "h": box_full.height},
                    "boxPx": rect_px,
                    "clickPointPx": {"x": click_x, "y": click_y},
                    "screenPoint": screen_point,
                    "config": best.get("config"),
                },
            }
        )

    out_img = debug_dir / f"ocr_anchor_dryrun_{_now_ms()}.png"
    _save_annotated_anchor_image(
        base_cgimage=cg,
        image_w=int(image_w),
        image_h=int(image_h),
        out_path=out_img,
        rects=rects,
        points=points,
    )

    payload: dict[str, Any] = {
        "meta": {
            "timestampMs": _now_ms(),
            "anchors": anchors_clean,
            "anchorRoi": anchor_roi,
            "matchMode": match_mode,
            "minConfidence": float(min_confidence),
            "offset": {"x": float(offset_x), "y": float(offset_y), "unit": offset_unit},
            "imageSize": {"width": image_w, "height": image_h},
            "capture": capture,
            "annotatedImage": str(out_img),
        },
        "matches": matches,
    }

    out_json = debug_dir / f"ocr_anchor_dryrun_{_now_ms()}.json"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(str(out_json))
    print(str(out_img))

    return payload


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", action="store_true", help="抓取酷狗窗口截图并评测")
    parser.add_argument("--image", type=str, default="", help="使用已有图片路径离线评测")

    parser.add_argument(
        "--anchor-dry-run",
        action="store_true",
        help="基于 OCR 锚点框计算点击点并输出标注（dry-run，不执行点击）",
    )
    parser.add_argument(
        "--anchors",
        type=str,
        default="",
        help="逗号分隔锚点列表；为空则使用默认锚点集合",
    )
    parser.add_argument(
        "--anchor-roi",
        type=str,
        default="auto",
        help="锚点搜索 ROI：auto/full/sidebar/top_search/tabs/result_list_top/bottom_player",
    )
    parser.add_argument(
        "--anchor-match",
        type=str,
        default="contains",
        choices=["contains", "exact"],
        help="锚点匹配模式",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.75,
        help="锚点匹配的最小置信度（默认 0.75，偏向高置信度）",
    )
    parser.add_argument(
        "--offset-x",
        type=float,
        default=0.0,
        help="点击点相对锚点框中心的 x 偏移",
    )
    parser.add_argument(
        "--offset-y",
        type=float,
        default=0.0,
        help="点击点相对锚点框中心的 y 偏移（向下为正）",
    )
    parser.add_argument(
        "--offset-unit",
        type=str,
        default="box",
        choices=["px", "box"],
        help="偏移单位：px=像素；box=按锚点框宽高比例",
    )

    args = parser.parse_args()

    ui = MacOSUIAutomation()

    capture: Optional[dict[str, Any]] = None
    image_path = str(args.image or "").strip()

    if args.capture:
        capture = await _capture_kugou(ui)
        image_path = str((capture or {}).get("screenshotPath") or "")

    if not image_path:
        raise RuntimeError("请提供 --capture 或 --image")

    debug_dir = _debug_dir()

    # Copy a stable reference for offline comparisons.
    ref_name = f"ocr_eval_ref_{_now_ms()}.png"
    ref_path = debug_dir / ref_name
    try:
        # best-effort copy
        ref_path.write_bytes(Path(image_path).read_bytes())
    except Exception:
        ref_path = Path(image_path)

    cg, w, h = _load_cgimage(str(ref_path))

    # ROIs (normalized, origin top-left)
    rois = {
        "full": (0.0, 0.0, 1.0, 1.0),
        "sidebar": (0.0, 0.18, 0.22, 0.72),
        "top_search": (0.18, 0.00, 0.78, 0.16),
        "tabs": (0.18, 0.12, 0.78, 0.16),
        "result_list_top": (0.18, 0.20, 0.78, 0.45),
        "bottom_player": (0.00, 0.86, 1.00, 0.14),
    }

    if args.anchor_dry_run:
        anchor_roi = str(args.anchor_roi or "auto").strip()
        if anchor_roi != "auto" and anchor_roi not in rois:
            raise RuntimeError(f"未知 ROI：{anchor_roi}，可选：auto/{'/'.join(sorted(rois.keys()))}")

        anchors = [a.strip() for a in str(args.anchors or "").split(",") if a.strip()]
        if not anchors:
            anchors = list(DEFAULT_ANCHORS)

        await _run_anchor_dry_run(
            cg=cg,
            image_w=w,
            image_h=h,
            capture=capture,
            rois=rois,
            anchors=anchors,
            anchor_roi=anchor_roi,
            match_mode=str(args.anchor_match),
            min_confidence=float(args.min_confidence),
            offset_x=float(args.offset_x),
            offset_y=float(args.offset_y),
            offset_unit=str(args.offset_unit),
            debug_dir=debug_dir,
        )
        return 0

    # Keywords we care about for UI automation.
    keywords = [
        "音乐",
        "我的",
        "搜索",
        "取消",
        "单曲",
        "歌单",
        "综合",
        "视频",
        "歌手",
    ]

    custom_words = keywords + ["播放", "暂停", "关注", "推荐", "歌单", "歌曲"]

    configs = []
    for accurate in [True, False]:
        for lang_corr in [True, False]:
            for grayscale in [True, False]:
                for scale in [1.0, 1.8, 2.4]:
                    configs.append(
                        {
                            "accurate": accurate,
                            "languageCorrection": lang_corr,
                            "grayscale": grayscale,
                            "scale": scale,
                            "languages": ["zh-Hans", "zh-Hant", "en-US"],
                            "minTextHeight": None,
                        }
                    )

    report: dict[str, Any] = {
        "meta": {
            "timestampMs": _now_ms(),
            "imagePath": str(ref_path),
            "capture": capture,
            "imageSize": {"width": w, "height": h},
            "keywords": keywords,
            "configs": len(configs),
            "rois": list(rois.keys()),
        },
        "cases": [],
    }

    # Run matrix
    for roi_name, roi in rois.items():
        crop = _crop_cgimage(cg, w=w, h=h, roi=roi)

        # save ROI snapshot for manual inspection
        try:
            from Foundation import NSURL
            from Quartz import (
                CGImageDestinationAddImage,
                CGImageDestinationCreateWithURL,
                CGImageDestinationFinalize,
            )

            out_path = debug_dir / f"ocr_roi_{roi_name}_{_now_ms()}.png"
            url = NSURL.fileURLWithPath_(str(out_path))
            dest = CGImageDestinationCreateWithURL(url, _png_type_identifier(), 1, None)
            if dest is not None:
                CGImageDestinationAddImage(dest, crop, None)
                CGImageDestinationFinalize(dest)
        except Exception:
            pass

        for cfg in configs:
            # Preprocess (Quartz/CoreGraphics only)
            cg2 = _preprocess_cg(
                crop,
                scale=float(cfg["scale"]),
                grayscale=bool(cfg["grayscale"]),
            )

            boxes = _vision_ocr(
                cg2,
                languages=list(cfg["languages"]),
                accurate=bool(cfg["accurate"]),
                language_correction=bool(cfg["languageCorrection"]),
                min_text_height=cfg["minTextHeight"],
                custom_words=custom_words,
            )

            scored = _score_keywords(boxes, keywords, min_conf=0.3)

            case = {
                "roi": roi_name,
                "roiRect": {"x": roi[0], "y": roi[1], "w": roi[2], "h": roi[3]},
                "config": cfg,
                "score": scored,
            }

            # Keep top boxes only to keep report small.
            top = sorted(boxes, key=lambda b: b.confidence, reverse=True)[:80]
            case["boxesTop"] = [
                {
                    "text": b.text,
                    "confidence": b.confidence,
                    "boxNormTL": {"x": b.x, "y": b.y, "w": b.width, "h": b.height},
                }
                for b in top
            ]

            report["cases"].append(case)

    out_report = debug_dir / f"ocr_eval_report_{_now_ms()}.json"
    out_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(str(out_report))
    # Quick summary: best case by total keyword hits and confidence.
    best = None
    for c in report["cases"]:
        hits = c["score"]["hits"]
        hit_count = sum(1 for v in hits.values() if v)
        conf_max = float(c["score"]["confMax"] or 0.0)
        key = (hit_count, conf_max)
        if best is None or key > best[0]:
            best = (key, c)

    if best:
        key, c = best
        print("BEST", {"roi": c["roi"], "config": c["config"], "hitCount": key[0], "confMax": key[1]})

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
