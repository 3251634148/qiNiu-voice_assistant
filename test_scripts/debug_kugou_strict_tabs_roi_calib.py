#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""严格 tabs ROI 调参/扫参脚本（酷狗）。

目标
- 为“必须出现且只能出现”的顶部 tabs 设计严格 ROI，并用 OCR 自动打分筛选 ROI。

支持两种模式
- my_tabs: 只允许出现「音乐 / 艺人 / 动态」
- recommend_tabs: 只允许出现「推荐 / 频道 / 歌单 / 歌手」

用法
- 现场抓取酷狗窗口并扫参：
  VOICE_ASSISTANT_DEBUG_RUN=strict_tabs_$(date +%s) backend_py/.venv/bin/python test_scripts/debug_kugou_strict_tabs_roi_calib.py --capture --mode my_tabs

- 对已有截图离线扫参：
  backend_py/.venv/bin/python test_scripts/debug_kugou_strict_tabs_roi_calib.py --image /path/to.png --mode my_tabs

输出
- `~/Documents/VoiceAssistant/ui_debug/<runId>/` 下会生成：
  - `strict_tabs_roi_top_*.png`: Top-N ROI 的裁剪图
  - `strict_tabs_roi_calib_*.json`: Top-N 候选 ROI、得分、OCR token 预览

说明
- 该脚本只做“裁剪 + OCR + 打分”，不做任何点击。
- ROI 使用归一化坐标（左上角原点）：(x, y, width, height)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend_py.services.macos_ui_automation import MacOSUIAutomation


KUGOU_APP_NAMES = ["酷狗音乐", "KugouMusic", "Kugou Music"]


@dataclass(frozen=True)
class RoiCandidate:
    roi: Tuple[float, float, float, float]
    ok: bool
    hits: int
    extra: int
    token_count: int
    tokens: Tuple[str, ...]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _debug_dir() -> Path:
    base = Path.home() / "Documents" / "VoiceAssistant" / "ui_debug"
    run_id = str(os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or "").strip()
    out = base / run_id if run_id else base
    out.mkdir(parents=True, exist_ok=True)
    return out


def _norm(value: str) -> str:
    v = str(value or "")
    v = "".join(v.split())
    v = v.replace("\uffff", "").replace("\ufffd", "")
    return v.strip()


def _clamp_roi(roi: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    rx, ry, rw, rh = (float(roi[0]), float(roi[1]), float(roi[2]), float(roi[3]))
    rx = max(0.0, min(1.0, rx))
    ry = max(0.0, min(1.0, ry))
    rw = max(0.0, min(1.0 - rx, rw))
    rh = max(0.0, min(1.0 - ry, rh))
    return (rx, ry, rw, rh)


def _iter_offsets(steps: Sequence[int]) -> Iterable[int]:
    for x in steps:
        yield int(x)


def _gen_grid(
    *,
    base: Tuple[float, float, float, float],
    step_x: float,
    step_y: float,
    step_w: float,
    step_h: float,
    span: int,
) -> List[Tuple[float, float, float, float]]:
    bx, by, bw, bh = base
    out: List[Tuple[float, float, float, float]] = []
    for ox in _iter_offsets(range(-span, span + 1)):
        for oy in _iter_offsets(range(-span, span + 1)):
            for ow in _iter_offsets(range(-span, span + 1)):
                for oh in _iter_offsets(range(-span, span + 1)):
                    roi = (
                        bx + float(ox) * float(step_x),
                        by + float(oy) * float(step_y),
                        bw + float(ow) * float(step_w),
                        bh + float(oh) * float(step_h),
                    )
                    roi = _clamp_roi(roi)
                    if roi[2] <= 0.01 or roi[3] <= 0.01:
                        continue
                    out.append(roi)
    # 去重（浮点近似，保守四舍五入）
    uniq = {}
    for r in out:
        k = (round(r[0], 4), round(r[1], 4), round(r[2], 4), round(r[3], 4))
        uniq[k] = r
    return list(uniq.values())


def _save_roi_crop_png(image_path: str, *, roi: Tuple[float, float, float, float], out_path: Path) -> None:
    """把归一化 ROI 裁成 PNG，便于肉眼确认 ROI 是否只包含目标 tabs。"""

    from Foundation import NSURL
    from Quartz import (
        CGImageDestinationAddImage,
        CGImageDestinationCreateWithURL,
        CGImageDestinationFinalize,
        CGImageGetHeight,
        CGImageGetWidth,
        CGImageSourceCreateImageAtIndex,
        CGImageSourceCreateWithURL,
        CGImageCreateWithImageInRect,
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

    rx, ry, rw, rh = _clamp_roi(roi)
    x = float(rx) * float(w)
    y = float(ry) * float(h)
    ww = float(rw) * float(w)
    hh = float(rh) * float(h)

    crop = CGImageCreateWithImageInRect(cg, ((x, y), (ww, hh)))
    if crop is None:
        crop = cg

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_url = NSURL.fileURLWithPath_(str(out_path))

    # 兼容部分 PyObjC 环境缺失 UniformTypeIdentifiers
    try:
        from UniformTypeIdentifiers import UTType

        png_ut = str(UTType.png().identifier())
    except Exception:
        png_ut = "public.png"

    dest = CGImageDestinationCreateWithURL(out_url, png_ut, 1, None)
    if dest is None:
        raise RuntimeError("无法创建图片输出目标")

    CGImageDestinationAddImage(dest, crop, None)
    CGImageDestinationFinalize(dest)


def _score_strict_tokens(
    *,
    tokens: Sequence[str],
    required: Sequence[str],
    min_conf_tokens: Sequence[str],
) -> Tuple[bool, int, int, int, Tuple[str, ...]]:
    # tokens: 原始 OCR tokens（已按 min_confidence 过滤）
    # min_conf_tokens: 同上（为未来扩展保留）
    req = [_norm(x) for x in required]
    req_set = set(req)

    got = [_norm(t) for t in tokens if _norm(t)]
    got_set = set(got)

    hits = sum(1 for t in req if t in got_set)
    extra = len([t for t in got_set if t not in req_set])

    ok = bool(hits == len(req_set) and extra == 0 and len(got_set) == len(req_set))
    return (ok, int(hits), int(extra), int(len(got_set)), tuple(sorted(got_set)))


async def _run(args: argparse.Namespace) -> None:
    ui = MacOSUIAutomation()
    debug_dir = _debug_dir()

    if bool(args.capture):
        cap = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=f"strict_tabs_capture_{args.mode}")
        image_path = str(cap.get("screenshotPath") or "")
    else:
        image_path = str(args.image or "")

    if not image_path:
        raise RuntimeError("未提供图片路径")

    modes = {
        "my_tabs": {
            "required": ["音乐", "艺人", "动态"],
            # 初始猜测：顶部左侧 tabs，尽量收紧。
            "base": (0.06, 0.00, 0.36, 0.08),
        },
        "recommend_tabs": {
            "required": ["推荐", "频道", "歌单", "歌手"],
            # 初始猜测：音乐主页二级 tabs（推荐/频道/歌单/歌手）。
            "base": (0.18, 0.12, 0.55, 0.08),
        },
    }

    if str(args.mode) not in modes:
        raise RuntimeError(f"未知 mode：{args.mode}")

    required = list(modes[str(args.mode)]["required"])
    base = tuple(modes[str(args.mode)]["base"])  # type: ignore[assignment]

    base = tuple(float(x) for x in (args.base or base))  # type: ignore[assignment]
    base = _clamp_roi(base)  # type: ignore[arg-type]

    grid = _gen_grid(
        base=base,
        step_x=float(args.step_x),
        step_y=float(args.step_y),
        step_w=float(args.step_w),
        step_h=float(args.step_h),
        span=int(args.span),
    )

    results: List[Dict[str, Any]] = []
    top: List[RoiCandidate] = []

    min_conf = float(args.min_conf)

    for idx, roi in enumerate(grid):
        boxes = await ui.ocr_screenshot_advanced(
            image_path,
            roi=roi,
            scale=float(args.ocr_scale),
            grayscale=bool(args.grayscale),
            accurate=bool(args.accurate),
            custom_words=required,
        )

        toks: List[str] = []
        for b in boxes:
            try:
                if float(getattr(b, "confidence", 0.0) or 0.0) < float(min_conf):
                    continue
            except Exception:
                continue
            t = _norm(str(getattr(b, "text", "") or ""))
            if t:
                toks.append(t)

        ok, hits, extra, token_count, token_set = _score_strict_tokens(
            tokens=toks,
            required=required,
            min_conf_tokens=toks,
        )

        cand = RoiCandidate(
            roi=roi,
            ok=bool(ok),
            hits=int(hits),
            extra=int(extra),
            token_count=int(token_count),
            tokens=tuple(token_set),
        )

        top.append(cand)

        # 只保留 topN：先按 ok，再按 hits，extra 越少越好，token_count 越少越好，面积越小越好。
        top.sort(
            key=lambda c: (
                bool(c.ok),
                int(c.hits),
                -int(c.extra),
                -int(c.token_count),
                -(float(c.roi[2]) * float(c.roi[3])),
                -float(c.roi[2]),
                -float(c.roi[3]),
                -float(c.roi[0]),
                -float(c.roi[1]),
            ),
            reverse=True,
        )
        top = top[: max(1, int(args.top_n))]

        if idx % 80 == 0:
            # 控制台轻量进度
            best = top[0]
            print(
                json.dumps(
                    {
                        "progress": {"idx": idx, "total": len(grid)},
                        "best": {
                            "ok": bool(best.ok),
                            "roi": best.roi,
                            "tokens": list(best.tokens),
                            "hits": best.hits,
                            "extra": best.extra,
                            "tokenCount": best.token_count,
                        },
                    },
                    ensure_ascii=False,
                )
            )

    # 落盘 topN 结果与裁剪图
    ts = _now_ms()
    out_items: List[Dict[str, Any]] = []
    for rank, cand in enumerate(top):
        crop_path = debug_dir / f"strict_tabs_roi_top_{args.mode}_{rank}_{ts}.png"
        try:
            _save_roi_crop_png(image_path, roi=cand.roi, out_path=crop_path)
        except Exception as e:
            crop_path = debug_dir / f"strict_tabs_roi_top_{args.mode}_{rank}_{ts}_CROP_FAIL.txt"
            crop_path.write_text(str(e), encoding="utf-8")

        out_items.append(
            {
                "rank": int(rank),
                "ok": bool(cand.ok),
                "roi": list(cand.roi),
                "tokens": list(cand.tokens),
                "hits": int(cand.hits),
                "extra": int(cand.extra),
                "tokenCount": int(cand.token_count),
                "crop": str(crop_path),
            }
        )

    payload = {
        "meta": {
            "timestampMs": int(ts),
            "mode": str(args.mode),
            "image": str(image_path),
            "base": list(base),
            "gridTotal": int(len(grid)),
            "minConf": float(min_conf),
            "ocr": {
                "scale": float(args.ocr_scale),
                "grayscale": bool(args.grayscale),
                "accurate": bool(args.accurate),
                "customWords": required,
            },
            "search": {
                "stepX": float(args.step_x),
                "stepY": float(args.step_y),
                "stepW": float(args.step_w),
                "stepH": float(args.step_h),
                "span": int(args.span),
                "topN": int(args.top_n),
            },
        },
        "top": out_items,
    }

    out_json = debug_dir / f"strict_tabs_roi_calib_{args.mode}_{ts}.json"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out_json), "top": out_items[:5]}, ensure_ascii=False, indent=2))


def _parse_roi(value: str) -> Tuple[float, float, float, float]:
    parts = [p.strip() for p in str(value or "").split(",") if p.strip()]
    if len(parts) != 4:
        raise ValueError("ROI 需要 4 个逗号分隔数字：x,y,w,h")
    return (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", action="store_true", help="抓取酷狗窗口")
    parser.add_argument("--image", type=str, default="", help="离线图片路径")
    parser.add_argument("--mode", type=str, default="my_tabs", choices=["my_tabs", "recommend_tabs"], help="扫参模式")

    parser.add_argument("--base", type=str, default="", help="自定义 base ROI，格式 x,y,w,h")
    parser.add_argument("--span", type=int, default=3, help="扫参跨度（每个维度 [-span, +span]）")
    parser.add_argument("--step-x", type=float, default=0.01, dest="step_x")
    parser.add_argument("--step-y", type=float, default=0.01, dest="step_y")
    parser.add_argument("--step-w", type=float, default=0.02, dest="step_w")
    parser.add_argument("--step-h", type=float, default=0.01, dest="step_h")

    parser.add_argument("--top-n", type=int, default=12, dest="top_n")
    parser.add_argument("--min-conf", type=float, default=0.6, dest="min_conf")

    parser.add_argument("--ocr-scale", type=float, default=3.2, dest="ocr_scale")
    parser.add_argument("--grayscale", action="store_true", default=True)
    parser.add_argument("--accurate", action="store_true", default=False)

    args = parser.parse_args()

    if not args.capture and not args.image:
        raise SystemExit("需要提供 --capture 或 --image")

    if args.base:
        args.base = _parse_roi(args.base)
    else:
        args.base = None

    import asyncio

    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
