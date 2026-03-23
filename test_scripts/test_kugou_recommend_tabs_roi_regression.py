#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""酷狗“推荐/频道/歌单/歌手”严格 tabs ROI 回归脚本。

目的
- 用一张窗口截图离线验证：当前 `music_recommend_tabs_strict` 的 ROI 能否稳定只命中
  「推荐/频道/歌单/歌手」四个 token（无多余 token）。

说明
- 该脚本不做任何点击，不依赖窗口激活；只对传入图片做 OCR。
- 支持通过环境变量覆盖 ROI（与业务逻辑一致）：
  - `KUGOU_RECOMMEND_TABS_STRICT_ROI="0.10,0.03,0.70,0.06"`

用法
- 直接验证（使用当前代码默认 ROI 或环境变量覆盖值）：
  VOICE_ASSISTANT_DEBUG_RUN=kugou_roi_reg_$(date +%s) \
    backend_py/.venv/bin/python test_scripts/test_kugou_recommend_tabs_roi_regression.py \
    --image /path/to/kugou.png

- 指定本次临时覆盖（脚本内部会设置 env 并 reload 模块，使 ROI 在本次进程内生效）：
  VOICE_ASSISTANT_DEBUG_RUN=kugou_roi_reg_$(date +%s) \
    backend_py/.venv/bin/python test_scripts/test_kugou_recommend_tabs_roi_regression.py \
    --image /path/to/kugou.png --env-roi "0.10,0.03,0.70,0.06"

退出码
- 0：通过
- 2：失败（会把 OCR token 与 missing/extra 等信息落盘到 ui_debug）
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


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


def _extract_tokens(
    boxes: Sequence[Any],
    *,
    min_confidence: float,
) -> List[str]:
    out: List[str] = []
    for b in boxes:
        try:
            conf = float(getattr(b, "confidence", 0.0) or 0.0)
        except Exception:
            conf = 0.0

        if conf < float(min_confidence):
            continue

        t = _norm(str(getattr(b, "text", "") or ""))
        if t:
            out.append(t)

    return out


def _load_recommend_tabs_roi() -> Tuple[float, float, float, float]:
    """按当前进程环境变量加载业务侧 ROI（通过 reload 使其生效）。"""

    from backend_py.services import music_controller as mc

    mc = importlib.reload(mc)
    roi = mc.KUGOU_ROIS["music_recommend_tabs_strict"]
    return (float(roi[0]), float(roi[1]), float(roi[2]), float(roi[3]))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=str, default="", help="待验证的酷狗窗口截图路径")
    parser.add_argument(
        "--env-roi",
        type=str,
        default="",
        help="本次进程内临时设置 KUGOU_RECOMMEND_TABS_STRICT_ROI（会 reload 模块）",
    )
    parser.add_argument("--min-conf", type=float, default=0.45, dest="min_conf")
    parser.add_argument("--ocr-scale", type=float, default=2.4, dest="ocr_scale")
    parser.add_argument("--grayscale", action="store_true", default=True)
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()

    image_path = str(args.image or "").strip()
    if not image_path:
        # 作为回归脚本：允许在没有样本图时“跳过但不失败”。
        print("SKIP: missing --image")
        return 0

    if args.env_roi:
        os.environ["KUGOU_RECOMMEND_TABS_STRICT_ROI"] = str(args.env_roi)

    roi = _load_recommend_tabs_roi()

    required_raw = ["推荐", "频道", "歌单", "歌手"]
    required = [_norm(x) for x in required_raw]
    required_set = set(required)

    from backend_py.services.macos_ui_automation import MacOSUIAutomation

    ui = MacOSUIAutomation()

    boxes = await ui.ocr_screenshot_advanced(
        image_path,
        roi=roi,
        scale=float(args.ocr_scale),
        grayscale=bool(args.grayscale),
        accurate=False,
        custom_words=required_raw,
    )

    tokens = _extract_tokens(boxes, min_confidence=float(args.min_conf))
    token_set = set(_norm(t) for t in tokens if _norm(t))

    missing = [t for t in required if t not in token_set]
    extra = [t for t in sorted(token_set) if t not in required_set]
    ok = bool((not missing) and (not extra) and len(token_set) == len(required_set))

    payload: Dict[str, Any] = {
        "ok": bool(ok),
        "meta": {
            "timestampMs": int(_now_ms()),
            "image": image_path,
            "roi": list(roi),
            "envRoi": str(args.env_roi or ""),
            "minConfidence": float(args.min_conf),
            "ocr": {
                "scale": float(args.ocr_scale),
                "grayscale": bool(args.grayscale),
                "accurate": False,
                "customWords": required_raw,
            },
        },
        "result": {
            "required": required_raw,
            "tokens": sorted(token_set),
            "missing": missing,
            "extra": extra,
            "preview": [str(getattr(b, "text", "") or "") for b in boxes[:20]],
        },
    }

    out_dir = _debug_dir()
    out_path = out_dir / f"kugou_recommend_tabs_roi_regression_{_now_ms()}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"ok": bool(ok), "out": str(out_path)}, ensure_ascii=False))
    return 0 if ok else 2


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
