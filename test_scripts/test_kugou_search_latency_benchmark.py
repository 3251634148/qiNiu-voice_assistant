#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""酷狗搜索链路“耗时阶段”基准/回归脚本。

目的
- 将 KuGou `music_ui(search)` 链路里最常见的慢点拆分为可计时的阶段（stage）。
- 调试产物与耗时统计按 runId 落盘到：`~/Documents/VoiceAssistant/ui_debug/<VOICE_ASSISTANT_DEBUG_RUN>/`。

默认行为（安全）
- 默认不做任何点击（只置前/截图/OCR/可选窗口归一化），避免高风险副作用。
- 如需计时“点击侧边栏音乐 / 点击搜索入口”等真实交互，请显式传入 `--do-click-*`。

用法示例
- 仅测量：置前 + 初始截图 +（可选）窗口归一化 + OCR（默认不点击）：
  VOICE_ASSISTANT_DEBUG_RUN=kugou_latency_$(date +%s) \
    backend_py/.venv/bin/python test_scripts/test_kugou_search_latency_benchmark.py

- 强制执行窗口归一化（即使已在目标尺寸/位置）：
  VOICE_ASSISTANT_DEBUG_RUN=kugou_latency_$(date +%s) \
    backend_py/.venv/bin/python test_scripts/test_kugou_search_latency_benchmark.py --normalize-mode always

- 禁用截图 PNG 无损重压缩（用于评估该步骤开销）：
  VOICE_ASSISTANT_DEBUG_RUN=kugou_latency_$(date +%s) \
    backend_py/.venv/bin/python test_scripts/test_kugou_search_latency_benchmark.py --disable-png-recompress

退出码
- 0：执行完成（即使某些阶段失败，也会落盘并返回 0，除非启用了预算断言）。
- 2：启用 `--assert-total-ms` 且总耗时超出预算。

注意
- 如启用窗口归一化，会改变酷狗窗口尺寸/位置（不修改数据，但会影响桌面布局）。
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend_py.services.macos_ui_automation import MacOSUIAutomation
from backend_py.services.music_controller import KUGOU_APP_NAMES, KUGOU_ROIS


@dataclass
class StageResult:
    name: str
    ok: bool
    duration_ms: int
    meta: Dict[str, Any]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ensure_run_id(run_id: str) -> str:
    existing = str(os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or "").strip()
    if existing:
        return existing

    run_id_clean = str(run_id or "").strip()
    if run_id_clean:
        os.environ["VOICE_ASSISTANT_DEBUG_RUN"] = run_id_clean
        return run_id_clean

    auto = f"kugou_latency_{int(time.time())}"
    os.environ["VOICE_ASSISTANT_DEBUG_RUN"] = auto
    return auto


def _debug_dir() -> Path:
    base = Path.home() / "Documents" / "VoiceAssistant" / "ui_debug"
    run_id = str(os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or "").strip()
    out = base / run_id if run_id else base
    out.mkdir(parents=True, exist_ok=True)
    return out


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KuGou search latency benchmark")
    parser.add_argument("--run-id", type=str, default="", help="如果 VOICE_ASSISTANT_DEBUG_RUN 为空，则使用该值")
    parser.add_argument(
        "--normalize-mode",
        type=str,
        default="auto",
        choices=["auto", "always", "never"],
        help="auto=若已满足目标尺寸/位置则跳过；always=强制归一化；never=从不归一化",
    )
    parser.add_argument(
        "--disable-png-recompress",
        action="store_true",
        help="进程内设置 VOICE_ASSISTANT_PNG_LOSSLESS_COMPRESS=0（用于评估重压缩开销）",
    )
    parser.add_argument(
        "--do-click-music",
        action="store_true",
        help="点击侧边栏“音乐”（高风险，谨慎使用）",
    )
    parser.add_argument(
        "--do-click-enter-search",
        action="store_true",
        help="在音乐主页尝试点击搜索入口（高风险，谨慎使用；依赖 OCR 命中）",
    )
    parser.add_argument(
        "--assert-total-ms",
        type=int,
        default=0,
        help="若 >0，且总耗时超过该值则返回码 2（用于回归/预算守卫）",
    )
    return parser.parse_args()


def _clamp_int(v: Any, *, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


def _is_already_normalized(*, ui: MacOSUIAutomation, cap: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    wb = cap.get("windowBounds") or {}
    if not isinstance(wb, dict):
        return (False, {"ok": False, "reason": "missing_windowBounds"})

    try:
        cur_w = int(round(float(wb.get("width") or 0.0)))
        cur_h = int(round(float(wb.get("height") or 0.0)))
        cur_x = int(round(float(wb.get("x") or 0.0)))
        cur_y = int(round(float(wb.get("y") or 0.0)))
    except Exception:
        return (False, {"ok": False, "reason": "invalid_windowBounds"})

    target_w = 1152
    target_h = 801

    try:
        screen = ui._get_main_screen_size()  # pylint: disable=protected-access
        target_x = int(max(0, round((float(screen.width) - float(target_w)) / 2.0)))
        target_y = int(max(0, round((float(screen.height) - float(target_h)) / 2.0)))
    except Exception as e:
        return (False, {"ok": False, "reason": "screen_size_unavailable", "error": str(e)})

    tol = 2
    size_ok = bool(abs(cur_w - target_w) <= tol and abs(cur_h - target_h) <= tol)
    pos_ok = bool(abs(cur_x - target_x) <= tol and abs(cur_y - target_y) <= tol)
    ok = bool(size_ok and pos_ok)

    return (
        ok,
        {
            "ok": bool(ok),
            "reason": "already_normalized" if ok else "mismatch",
            "tolerancePx": int(tol),
            "current": {"x": cur_x, "y": cur_y, "width": cur_w, "height": cur_h},
            "target": {"x": target_x, "y": target_y, "width": int(target_w), "height": int(target_h)},
            "screen": {"width": int(screen.width), "height": int(screen.height)},
        },
    )


def _norm_text(s: str) -> str:
    v = "".join(str(s or "").split())
    v = v.replace("\uffff", "").replace("\ufffd", "")
    return v.strip().lower()


def _extract_ts_ms_from_path(path: str) -> Optional[int]:
    m = re.search(r"_(\d{10,})\\.png$", str(path or ""))
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


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


async def _maybe_click_music(
    *,
    ui: MacOSUIAutomation,
    cap: Dict[str, Any],
    stages: List[StageResult],
) -> None:
    t0 = time.perf_counter()
    ok = True
    meta: Dict[str, Any] = {"clicked": False}

    try:
        screenshot_path = str(cap.get("screenshotPath") or "")
        if not screenshot_path:
            raise RuntimeError("missing_screenshotPath")

        boxes = await ui.ocr_screenshot_advanced(
            screenshot_path,
            roi=KUGOU_ROIS["sidebar_music"],
            scale=3.2,
            grayscale=True,
            accurate=False,
            custom_words=["音乐"],
        )
        meta["ocrPreview"] = [str(getattr(b, "text", "") or "") for b in boxes[:10]]

        music_boxes = [b for b in boxes if "音乐" in _norm_text(str(getattr(b, "text", "") or ""))]
        if not music_boxes:
            raise RuntimeError("ocr_not_found_music")

        music_box = min(
            music_boxes,
            key=lambda b: (
                float(getattr(b, "y", 0.0) or 0.0),
                float(getattr(b, "x", 0.0) or 0.0),
                -(float(getattr(b, "confidence", 0.0) or 0.0)),
            ),
        )

        cx, cy = music_box.center()
        sx, sy = ui._to_screen_point_from_window_image_point(  # pylint: disable=protected-access
            float(cx),
            float(cy),
            window_bounds=cap.get("windowBounds") or {},
            image_size=cap.get("imageSize") or {},
        )

        await _bring_kugou_front(ui)
        await asyncio.sleep(0.08)
        await ui.click_at(float(sx), float(sy), clicks=1)
        meta["clicked"] = True
        meta["screenPoint"] = {"x": float(sx), "y": float(sy)}
        meta["imagePoint"] = {"x": float(cx), "y": float(cy)}
        meta["box"] = {
            "text": str(getattr(music_box, "text", "") or ""),
            "confidence": float(getattr(music_box, "confidence", 0.0) or 0.0),
            "x": float(getattr(music_box, "x", 0.0) or 0.0),
            "y": float(getattr(music_box, "y", 0.0) or 0.0),
            "width": float(getattr(music_box, "width", 0.0) or 0.0),
            "height": float(getattr(music_box, "height", 0.0) or 0.0),
        }
    except Exception as e:
        ok = False
        meta["error"] = str(e)

    dt = int(round((time.perf_counter() - t0) * 1000.0))
    stages.append(StageResult(name="click_music", ok=bool(ok), duration_ms=dt, meta=meta))


async def _maybe_click_enter_search(
    *,
    ui: MacOSUIAutomation,
    cap: Dict[str, Any],
    stages: List[StageResult],
) -> None:
    t0 = time.perf_counter()
    ok = True
    meta: Dict[str, Any] = {"clicked": False}

    try:
        screenshot_path = str(cap.get("screenshotPath") or "")
        if not screenshot_path:
            raise RuntimeError("missing_screenshotPath")

        boxes = await ui.ocr_screenshot_advanced(
            screenshot_path,
            roi=KUGOU_ROIS["top_search_verify"],
            scale=3.2,
            grayscale=True,
            accurate=False,
            custom_words=["搜索", "取消", "历史", "历史搜索"],
        )
        meta["ocrPreview"] = [str(getattr(b, "text", "") or "") for b in boxes[:12]]

        search_boxes = [b for b in boxes if "搜索" in _norm_text(str(getattr(b, "text", "") or ""))]
        if not search_boxes:
            raise RuntimeError("ocr_not_found_search")

        search_box = min(
            search_boxes,
            key=lambda b: (
                float(getattr(b, "y", 0.0) or 0.0),
                float(getattr(b, "x", 0.0) or 0.0),
                -(float(getattr(b, "confidence", 0.0) or 0.0)),
            ),
        )

        cx, cy = search_box.center()
        sx, sy = ui._to_screen_point_from_window_image_point(  # pylint: disable=protected-access
            float(cx),
            float(cy),
            window_bounds=cap.get("windowBounds") or {},
            image_size=cap.get("imageSize") or {},
        )

        await _bring_kugou_front(ui)
        await asyncio.sleep(0.08)
        await ui.click_at(float(sx), float(sy), clicks=1)
        meta["clicked"] = True
        meta["screenPoint"] = {"x": float(sx), "y": float(sy)}
        meta["imagePoint"] = {"x": float(cx), "y": float(cy)}
        meta["box"] = {
            "text": str(getattr(search_box, "text", "") or ""),
            "confidence": float(getattr(search_box, "confidence", 0.0) or 0.0),
            "x": float(getattr(search_box, "x", 0.0) or 0.0),
            "y": float(getattr(search_box, "y", 0.0) or 0.0),
            "width": float(getattr(search_box, "width", 0.0) or 0.0),
            "height": float(getattr(search_box, "height", 0.0) or 0.0),
        }
    except Exception as e:
        ok = False
        meta["error"] = str(e)

    dt = int(round((time.perf_counter() - t0) * 1000.0))
    stages.append(StageResult(name="click_enter_search", ok=bool(ok), duration_ms=dt, meta=meta))


async def _run_once(*, args: argparse.Namespace) -> Dict[str, Any]:
    ui = MacOSUIAutomation()
    stages: List[StageResult] = []

    t0_total = time.perf_counter()

    # 0) ensure_accessibility_ready
    t0 = time.perf_counter()
    ok = True
    meta: Dict[str, Any] = {}
    try:
        await ui.ensure_accessibility_ready()
    except Exception as e:
        ok = False
        meta["error"] = str(e)
    stages.append(
        StageResult(
            name="ensure_accessibility_ready",
            ok=bool(ok),
            duration_ms=int(round((time.perf_counter() - t0) * 1000.0)),
            meta=meta,
        )
    )

    # 1) bring_kugou_front
    t0 = time.perf_counter()
    ok = True
    meta = {}
    try:
        await _bring_kugou_front(ui)
        await asyncio.sleep(0.12)
        meta["frontmost"] = await ui.get_frontmost_process_name()
    except Exception as e:
        ok = False
        meta["error"] = str(e)
    stages.append(
        StageResult(
            name="bring_kugou_front",
            ok=bool(ok),
            duration_ms=int(round((time.perf_counter() - t0) * 1000.0)),
            meta=meta,
        )
    )

    # 2) screenshot_init
    t0 = time.perf_counter()
    ok = True
    meta = {}
    cap_init: Optional[Dict[str, Any]] = None
    try:
        cap_init = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_latency_init")
        meta["capture"] = cap_init
    except Exception as e:
        ok = False
        meta["error"] = str(e)
    stages.append(
        StageResult(
            name="screenshot_init",
            ok=bool(ok),
            duration_ms=int(round((time.perf_counter() - t0) * 1000.0)),
            meta=meta,
        )
    )

    # 3) normalize_window (optional)
    t0 = time.perf_counter()
    ok = True
    meta = {"mode": str(args.normalize_mode)}

    did_normalize = False
    normalize_skip_info: Dict[str, Any] = {"ok": False, "reason": "not_evaluated"}

    try:
        if str(args.normalize_mode) == "never":
            normalize_skip_info = {"ok": True, "skipped": True, "reason": "normalize_mode_never"}
        elif cap_init is None:
            did_normalize = True
        elif str(args.normalize_mode) == "always":
            did_normalize = True
            normalize_skip_info = {"ok": True, "skipped": False, "reason": "normalize_mode_always"}
        else:
            already_ok, info = _is_already_normalized(ui=ui, cap=cap_init)
            normalize_skip_info = info
            if not already_ok:
                did_normalize = True

        meta["skipDecision"] = normalize_skip_info

        if did_normalize:
            norm = await ui.normalize_process_window(
                process_name="酷狗音乐",
                width=1152,
                height=801,
                center_main_screen=True,
            )
            meta["normalizeResult"] = norm
    except Exception as e:
        ok = False
        meta["error"] = str(e)

    stages.append(
        StageResult(
            name="normalize_window",
            ok=bool(ok),
            duration_ms=int(round((time.perf_counter() - t0) * 1000.0)),
            meta=meta,
        )
    )

    # 4) screenshot_after_normalize
    t0 = time.perf_counter()
    ok = True
    meta = {"didNormalize": bool(did_normalize)}
    cap_norm: Optional[Dict[str, Any]] = None
    try:
        await asyncio.sleep(0.18)
        cap_norm = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_latency_after_norm")
        meta["capture"] = cap_norm
    except Exception as e:
        ok = False
        meta["error"] = str(e)
    stages.append(
        StageResult(
            name="screenshot_after_normalize",
            ok=bool(ok),
            duration_ms=int(round((time.perf_counter() - t0) * 1000.0)),
            meta=meta,
        )
    )

    # 5) ocr_sidebar_music (can reuse cap_norm)
    t0 = time.perf_counter()
    ok = True
    meta = {}
    try:
        cap_for_ocr = cap_norm if cap_norm is not None else cap_init
        screenshot_path = str((cap_for_ocr or {}).get("screenshotPath") or "")
        if not screenshot_path:
            raise RuntimeError("missing_screenshotPath")

        boxes = await ui.ocr_screenshot_advanced(
            screenshot_path,
            roi=KUGOU_ROIS["sidebar_music"],
            scale=3.2,
            grayscale=True,
            accurate=False,
            custom_words=["音乐", "视频", "我的"],
        )
        meta["count"] = int(len(list(boxes or [])))
        meta["preview"] = [str(getattr(b, "text", "") or "") for b in boxes[:18]]
        meta["image"] = screenshot_path
        meta["roi"] = list(KUGOU_ROIS["sidebar_music"])

        # 估算 OCR 使用的截图“新鲜度”。
        ts_hint = _extract_ts_ms_from_path(screenshot_path)
        if ts_hint is not None:
            meta["screenshotAgeMs"] = int(max(0, _now_ms() - int(ts_hint)))
    except Exception as e:
        ok = False
        meta["error"] = str(e)

    stages.append(
        StageResult(
            name="ocr_sidebar_music",
            ok=bool(ok),
            duration_ms=int(round((time.perf_counter() - t0) * 1000.0)),
            meta=meta,
        )
    )

    # 6) optional: click music
    if bool(args.do_click_music) and cap_norm is not None:
        await _maybe_click_music(ui=ui, cap=cap_norm, stages=stages)

        t0 = time.perf_counter()
        ok = True
        meta = {}
        try:
            await asyncio.sleep(0.28)
            cap_after = await ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_latency_after_click_music")
            meta["capture"] = cap_after
        except Exception as e:
            ok = False
            meta["error"] = str(e)
        stages.append(
            StageResult(
                name="screenshot_after_click_music",
                ok=bool(ok),
                duration_ms=int(round((time.perf_counter() - t0) * 1000.0)),
                meta=meta,
            )
        )

        # 7) optional: click enter search
        if bool(args.do_click_enter_search):
            cap_for_search = meta.get("capture") if isinstance(meta.get("capture"), dict) else None
            if cap_for_search is None:
                cap_for_search = cap_norm

            await _maybe_click_enter_search(ui=ui, cap=cap_for_search, stages=stages)

            t0 = time.perf_counter()
            ok = True
            meta2: Dict[str, Any] = {}
            try:
                await asyncio.sleep(0.35)
                cap_after = await ui.screenshot_window(
                    owner_names=KUGOU_APP_NAMES,
                    tag="kugou_latency_after_click_search",
                )
                meta2["capture"] = cap_after
            except Exception as e:
                ok = False
                meta2["error"] = str(e)
            stages.append(
                StageResult(
                    name="screenshot_after_click_search",
                    ok=bool(ok),
                    duration_ms=int(round((time.perf_counter() - t0) * 1000.0)),
                    meta=meta2,
                )
            )

    total_ms = int(round((time.perf_counter() - t0_total) * 1000.0))

    payload: Dict[str, Any] = {
        "meta": {
            "timestampMs": int(_now_ms()),
            "runId": str(os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or ""),
            "args": {
                "normalizeMode": str(args.normalize_mode),
                "disablePngRecompress": bool(args.disable_png_recompress),
                "doClickMusic": bool(args.do_click_music),
                "doClickEnterSearch": bool(args.do_click_enter_search),
                "assertTotalMs": _clamp_int(getattr(args, "assert_total_ms", 0), default=0),
            },
            "env": {
                "VOICE_ASSISTANT_PNG_LOSSLESS_COMPRESS": str(os.environ.get("VOICE_ASSISTANT_PNG_LOSSLESS_COMPRESS") or ""),
            },
        },
        "totalMs": int(total_ms),
        "stages": [
            {
                "name": s.name,
                "ok": bool(s.ok),
                "durationMs": int(s.duration_ms),
                "meta": s.meta,
            }
            for s in stages
        ],
    }

    out_dir = _debug_dir()
    out_path = out_dir / f"kugou_search_latency_benchmark_{_now_ms()}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    payload["out"] = str(out_path)
    return payload


async def _run() -> int:
    args = _parse_args()
    _ensure_run_id(str(args.run_id or ""))

    if bool(args.disable_png_recompress):
        os.environ["VOICE_ASSISTANT_PNG_LOSSLESS_COMPRESS"] = "0"

    result = await _run_once(args=args)

    print(json.dumps({"ok": True, "out": result.get("out"), "totalMs": result.get("totalMs")}, ensure_ascii=False))

    budget = _clamp_int(getattr(args, "assert_total_ms", 0), default=0)
    if budget > 0:
        total_ms = _clamp_int(result.get("totalMs"), default=0)
        if total_ms > budget:
            return 2

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
