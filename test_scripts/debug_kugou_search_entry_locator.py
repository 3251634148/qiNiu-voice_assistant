#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""验证酷狗搜索入口（top_search ROI）OCR 可识别性。

用法
- 现场抓取酷狗窗口并分析：
  VOICE_ASSISTANT_DEBUG_RUN=search_entry_$(date +%s) backend_py/.venv/bin/python test_scripts/debug_kugou_search_entry_locator.py --capture

- 对已有截图离线分析：
  backend_py/.venv/bin/python test_scripts/debug_kugou_search_entry_locator.py --image /path/to.png

输出
- `~/Documents/VoiceAssistant/ui_debug/<runId>/` 下会生成：
  - `top_search_roi_*.png`：裁剪出的 top_search ROI 图
  - `top_search_ocr_*.json`：ROI 内 OCR boxes

说明
- 搜索 icon 通常是纯图标，OCR 可能识别不到；该脚本用于确认“搜索/取消”等文字锚点是否稳定。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend_py.services.macos_ui_automation import MacOSUIAutomation


KUGOU_APP_NAMES = ["酷狗音乐", "KugouMusic", "Kugou Music"]
TOP_SEARCH_ROI = (0.18, 0.0, 0.78, 0.16)
# 收紧的搜索框文本区域 ROI（右上角）。
# 故意比 TOP_SEARCH_ROI 窄，以减少 tabs/cards 带来的 OCR 干扰。
SEARCH_BAR_ROI = (0.60, 0.00, 0.36, 0.12)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _debug_dir() -> Path:
    base = Path.home() / "Documents" / "VoiceAssistant" / "ui_debug"
    run_id = str(os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or "").strip()
    out = base / run_id if run_id else base
    out.mkdir(parents=True, exist_ok=True)
    return out


def _png_type_identifier() -> str:
    try:
        from UniformTypeIdentifiers import UTType

        return str(UTType.png().identifier())
    except Exception:
        return "public.png"


def _load_cgimage(image_path: str) -> tuple[Any, int, int]:
    from Foundation import NSURL
    from Quartz import (
        CGImageGetHeight,
        CGImageGetWidth,
        CGImageSourceCreateImageAtIndex,
        CGImageSourceCreateWithURL,
    )

    url = NSURL.fileURLWithPath_(str(image_path))
    src = CGImageSourceCreateWithURL(url, None)
    if src is None:
        raise RuntimeError(f"无法加载图片：{image_path}")

    cg = CGImageSourceCreateImageAtIndex(src, 0, None)
    if cg is None:
        raise RuntimeError(f"无法获取 CGImage：{image_path}")

    w = int(CGImageGetWidth(cg))
    h = int(CGImageGetHeight(cg))
    if w <= 1 or h <= 1:
        raise RuntimeError("图片尺寸无效")

    return (cg, w, h)


def _crop_cgimage(cgimage: Any, *, image_w: int, image_h: int, roi: tuple[float, float, float, float]) -> Any:
    from Quartz import CGImageCreateWithImageInRect

    rx, ry, rw, rh = roi
    rx = max(0.0, min(1.0, float(rx)))
    ry = max(0.0, min(1.0, float(ry)))
    rw = max(0.0, min(1.0 - rx, float(rw)))
    rh = max(0.0, min(1.0 - ry, float(rh)))

    x = float(rx) * float(image_w)
    y = float(ry) * float(image_h)
    ww = float(rw) * float(image_w)
    hh = float(rh) * float(image_h)

    rect = ((x, y), (ww, hh))
    crop = CGImageCreateWithImageInRect(cgimage, rect)
    if crop is None:
        return cgimage
    return crop


def _save_cgimage_png(cgimage: Any, *, out_path: Path) -> None:
    from Foundation import NSURL
    from Quartz import (
        CGImageDestinationAddImage,
        CGImageDestinationCreateWithURL,
        CGImageDestinationFinalize,
    )

    url = NSURL.fileURLWithPath_(str(out_path))
    dest = CGImageDestinationCreateWithURL(url, _png_type_identifier(), 1, None)
    if dest is None:
        raise RuntimeError("无法创建图片输出目标")

    CGImageDestinationAddImage(dest, cgimage, None)
    CGImageDestinationFinalize(dest)


async def _run(args: argparse.Namespace) -> None:
    ui = MacOSUIAutomation()
    debug_dir = _debug_dir()

    if args.capture:
        cap = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_search_entry_capture")
        image_path = str(cap.get("screenshotPath") or "")
    else:
        image_path = str(args.image or "")

    if not image_path:
        raise RuntimeError("未提供图片路径")

    cg, w, h = _load_cgimage(image_path)

    rois = {
        "top_search": TOP_SEARCH_ROI,
        "search_bar": SEARCH_BAR_ROI,
    }

    crop_paths: dict[str, str] = {}
    for name, roi in rois.items():
        crop = _crop_cgimage(cg, image_w=w, image_h=h, roi=roi)
        out_img = debug_dir / f"{name}_roi_{_now_ms()}.png"
        _save_cgimage_png(crop, out_path=out_img)
        crop_paths[name] = str(out_img)

    # 注意：当前酷狗 UI 中 placeholder "搜索"对比度较低。
    # 我们同时验证宽 ROI（top_search）和窄 ROI（search_bar）。
    roi_configs = {
        "top_search": [
            {"scale": 1.0, "grayscale": True, "accurate": False},
            {"scale": 2.4, "grayscale": True, "accurate": False},
            {"scale": 2.4, "grayscale": False, "accurate": False},
        ],
        "search_bar": [
            # 经验证，低对比度 placeholder 文本用这个配置效果更好。
            {"scale": 3.2, "grayscale": True, "accurate": False},
            {"scale": 3.2, "grayscale": False, "accurate": False},
        ],
    }

    custom_words = ["搜索", "取消", "历史搜索", "推荐", "频道", "歌单", "歌手"]

    cases: list[dict[str, Any]] = []
    all_payload: list[dict[str, Any]] = []

    for roi_name, cfgs in roi_configs.items():
        roi = rois[roi_name]
        for cfg in cfgs:
            boxes = await ui.ocr_screenshot_advanced(
                image_path,
                roi=roi,
                scale=float(cfg["scale"]),
                grayscale=bool(cfg["grayscale"]),
                accurate=bool(cfg["accurate"]),
                custom_words=custom_words,
            )

            payload = [
                {
                    "text": b.text,
                    "confidence": b.confidence,
                    "x": b.x,
                    "y": b.y,
                    "width": b.width,
                    "height": b.height,
                }
                for b in boxes
            ]

            for p in payload:
                p["roi"] = roi_name
                p["config"] = cfg

            all_payload.extend(payload)

            cases.append(
                {
                    "roi": roi_name,
                    "roiRect": roi,
                    "config": cfg,
                    "boxCount": len(payload),
                    "preview": [str(p.get("text") or "") for p in payload[:12]],
                    "hits": {
                        "search": any("搜索" in str(p.get("text") or "") for p in payload),
                        "cancel": any("取消" in str(p.get("text") or "") for p in payload),
                        "history": any("历史搜索" in str(p.get("text") or "") for p in payload),
                    },
                }
            )

    out_json = debug_dir / f"top_search_ocr_{_now_ms()}.json"
    out_json.write_text(json.dumps({"rois": rois, "cases": cases, "boxes": all_payload}, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "image": image_path,
        "rois": rois,
        "crops": crop_paths,
        "ocr": str(out_json),
        "cases": cases,
        "boxCount": len(all_payload),
        "hits": {
            "search": any("搜索" in str(p.get("text") or "") for p in all_payload),
            "cancel": any("取消" in str(p.get("text") or "") for p in all_payload),
            "history": any("历史搜索" in str(p.get("text") or "") for p in all_payload),
        },
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", action="store_true", help="抓取酷狗窗口")
    parser.add_argument("--image", type=str, default="", help="离线图片路径")
    args = parser.parse_args()

    if not args.capture and not args.image:
        raise SystemExit("需要提供 --capture 或 --image")

    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
