#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""验证酷狗遮挡窗口（小窗/大窗）的裁剪与“标题锚点”OCR 定位。

用法
- 现场抓取酷狗窗口并分析：
  VOICE_ASSISTANT_DEBUG_RUN=panel_back_$(date +%s) backend_py/.venv/bin/python test_scripts/debug_kugou_panel_back_locator.py --capture

- 对已有截图离线分析：
  backend_py/.venv/bin/python test_scripts/debug_kugou_panel_back_locator.py --image /path/to.png

输出
- `~/Documents/VoiceAssistant/ui_debug/<runId>/` 下会生成：
  - `panel_roi_*.png`：裁剪出的 ROI 图
  - `panel_ocr_*.json`：ROI 内 OCR boxes

说明
- 本脚本仅做“裁剪 + OCR 定位返回按钮”验证，不做任何业务动作。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

# 确保以脚本方式运行时能导入项目根目录下的 backend_py 模块。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend_py.services.macos_ui_automation import MacOSUIAutomation


KUGOU_APP_NAMES = ["酷狗音乐", "KugouMusic", "Kugou Music"]


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


def _pick_title_anchor(boxes: list[Any]) -> Optional[dict[str, Any]]:
    """从遮挡窗口 ROI 内选取一个"标题锚点"框（靠左上的文本框）。

    不再依赖 OCR 识别返回图标（< / ←），而是找到 panel 头部任意可见的标题文本，
    然后点击其左侧来触发返回。
    """

    def _norm(value: str) -> str:
        v = str(value or "")
        v = "".join(v.split())
        v = v.replace("\uffff", "").replace("\ufffd", "")
        return v.strip().lower()

    candidates: list[Any] = []
    for b in boxes:
        text = _norm(str(getattr(b, "text", "") or ""))
        if not text:
            continue

        try:
            conf = float(getattr(b, "confidence", 0.0) or 0.0)
        except Exception:
            conf = 0.0

        if conf < 0.35:
            continue

        candidates.append(b)

    if not candidates:
        return None

    title = min(
        candidates,
        key=lambda b: (
            float(getattr(b, "y", 0.0) or 0.0),
            float(getattr(b, "x", 0.0) or 0.0),
            -(float(getattr(b, "confidence", 0.0) or 0.0)),
        ),
    )

    return {
        "text": str(getattr(title, "text", "") or ""),
        "confidence": float(getattr(title, "confidence", 0.0) or 0.0),
        "x": float(getattr(title, "x", 0.0) or 0.0),
        "y": float(getattr(title, "y", 0.0) or 0.0),
        "width": float(getattr(title, "width", 0.0) or 0.0),
        "height": float(getattr(title, "height", 0.0) or 0.0),
    }


async def _run(args: argparse.Namespace) -> None:
    ui = MacOSUIAutomation()
    debug_dir = _debug_dir()

    if args.capture:
        cap = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_panel_back_capture")
        image_path = str(cap.get("screenshotPath") or "")
    else:
        image_path = str(args.image or "")

    if not image_path:
        raise RuntimeError("未提供图片路径")

    cg, w, h = _load_cgimage(image_path)

    # 候选遮挡窗口顶部 ROI（归一化坐标）
    rois = {
        "right_small_panel_top": (0.42, 0.0, 0.58, 0.18),
        "large_panel_top": (0.18, 0.0, 0.82, 0.18),
    }

    summary: dict[str, Any] = {"image": image_path, "w": w, "h": h, "rois": {}, "back": {}}

    for name, roi in rois.items():
        crop = _crop_cgimage(cg, image_w=w, image_h=h, roi=roi)
        out_img = debug_dir / f"panel_roi_{name}_{_now_ms()}.png"
        _save_cgimage_png(crop, out_path=out_img)

        boxes = await ui.ocr_screenshot_advanced(
            image_path,
            roi=roi,
            scale=1.0,
            grayscale=True,
            accurate=False,
            custom_words=["推荐", "频道", "歌单", "歌手", "音乐", "搜索", "取消", "播放全部"],
        )

        out_json = debug_dir / f"panel_ocr_{name}_{_now_ms()}.json"
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
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        summary["rois"][name] = {
            "roi": roi,
            "crop": str(out_img),
            "ocr": str(out_json),
            "boxCount": len(payload),
        }

        summary["back"][name] = _pick_title_anchor(boxes)

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
