#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""KuGou VLM driver dry-run 脚本（安全：不点击不键入）。

目的
- 验证 `backend_py/services/vlm_ui_driver.py`：截图 → 缩放 → base64 → Ollama → 解析 UI_ACTION_JSON。
- 不执行任何点击/键入（dry-run），只产出证据链，便于调参与定位问题。

产物
- `~/Documents/VoiceAssistant/ui_debug/<VOICE_ASSISTANT_DEBUG_RUN>/` 下会落盘：
  - `kugou_vlm_step_*_request_*.json` / `kugou_vlm_step_*_response_*.json`
  - 缩放后的输入图 `*_img_*.png`

用法示例
- 使用 `.env` 默认配置：
  VOICE_ASSISTANT_DEBUG_RUN=kugou_vlm_dry_$(date +%s) \
    python test_scripts/debug_kugou_vlm_driver_dry_run.py --query "周杰伦 稻香"

- 临时覆盖 steps 与 image_max_side：
  VOICE_ASSISTANT_DEBUG_RUN=kugou_vlm_dry_$(date +%s) \
    python test_scripts/debug_kugou_vlm_driver_dry_run.py --query "周杰伦 稻香" --max-steps 6 --image-max-side 512

注意
- 该脚本会尝试把酷狗置前并截图，需要 macOS 辅助功能权限。
- VLM driver 现在要求输入图片为 WebP（quality=95）。进程会把窗口截图（PNG）转换为 WebP 后再喂给模型。
  - 若本机缺少 WebP 编码能力（推荐 `cwebp`），driver 会 fail-fast 并把错误原因落盘到 `ui_debug/<runId>/`。
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
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KuGou VLM driver dry-run")
    parser.add_argument("--query", type=str, default="", help="搜索词，例如：周杰伦 稻香")
    parser.add_argument("--run-id", type=str, default="", help="如果 VOICE_ASSISTANT_DEBUG_RUN 为空，则使用该值")
    parser.add_argument("--max-steps", type=int, default=0, help="临时覆盖 VOICE_ASSISTANT_VLM_MAX_STEPS")
    parser.add_argument("--image-max-side", type=int, default=0, help="临时覆盖 VOICE_ASSISTANT_VLM_IMAGE_MAX_SIDE")
    parser.add_argument("--model", type=str, default="", help="临时覆盖 VOICE_ASSISTANT_VLM_MODEL")
    parser.add_argument("--base-url", type=str, default="", help="临时覆盖 VOICE_ASSISTANT_OLLAMA_BASE_URL")
    return parser.parse_args()


def _debug_dir() -> Path:
    base = Path.home() / "Documents" / "VoiceAssistant" / "ui_debug"
    run_id = str(os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or "").strip()
    out = base / run_id if run_id else base
    out.mkdir(parents=True, exist_ok=True)
    return out


async def _run() -> int:
    args = _parse_args()

    query = str(args.query or "").strip()
    if not query:
        print("missing --query")
        return 2

    if not str(os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or "").strip():
        run_id = str(args.run_id or "").strip() or f"kugou_vlm_dry_{int(time.time() * 1000)}"
        os.environ["VOICE_ASSISTANT_DEBUG_RUN"] = run_id

    # 强制使用 VLM 模式（driver 仅用于该模式的独立调试）。
    os.environ["VOICE_ASSISTANT_UI_AUTOMATION_MODE"] = "vlm"

    if int(args.max_steps or 0) > 0:
        os.environ["VOICE_ASSISTANT_VLM_MAX_STEPS"] = str(int(args.max_steps))
    if int(args.image_max_side or 0) > 0:
        os.environ["VOICE_ASSISTANT_VLM_IMAGE_MAX_SIDE"] = str(int(args.image_max_side))
    if str(args.model or "").strip():
        os.environ["VOICE_ASSISTANT_VLM_MODEL"] = str(args.model).strip()
    if str(args.base_url or "").strip():
        os.environ["VOICE_ASSISTANT_OLLAMA_BASE_URL"] = str(args.base_url).strip()

    # 关键：config 是单例；这里用 reload 让本次进程内覆盖值生效。
    import backend_py.config_manager as cm

    cm = importlib.reload(cm)

    import backend_py.services.vlm_ui_driver as vd

    vd = importlib.reload(vd)

    from backend_py.services.macos_ui_automation import MacOSUIAutomation

    ui = MacOSUIAutomation()
    driver = vd.VlmUiDriver(ui=ui)

    try:
        result = await driver.run_kugou_search_play(query=query, debug=True, dry_run=True)
    except Exception as e:
        out_path = _debug_dir() / f"kugou_vlm_dry_run_error_{int(time.time() * 1000)}.json"
        err = str(e).strip() or repr(e)
        payload: Dict[str, Any] = {
            "error": err,
            "query": query,
            "config": {
                "mode": cm.UI_AUTOMATION_CONFIG.mode,
                "baseUrl": cm.UI_AUTOMATION_CONFIG.ollama_base_url,
                "model": cm.UI_AUTOMATION_CONFIG.vlm_model,
                "maxSteps": cm.UI_AUTOMATION_CONFIG.vlm_max_steps,
                "imageMaxSide": cm.UI_AUTOMATION_CONFIG.vlm_image_max_side,
            },
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[vlm-dry] failed: {e}")
        print(f"[vlm-dry] out={out_path}")
        return 2

    out_path = _debug_dir() / f"kugou_vlm_dry_run_result_{int(time.time() * 1000)}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[vlm-dry] ok. runId={str(os.environ.get('VOICE_ASSISTANT_DEBUG_RUN') or '').strip()}")
    print(f"[vlm-dry] out={out_path}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
