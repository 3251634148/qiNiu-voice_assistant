#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Ollama /api/chat smoke 测试（本地 VLM 基础连通性）。

目的
- 验证本机 Ollama 服务可访问。
- 验证指定模型可被调用并返回可读文本。
- 产物按 runId 落盘到：`~/Documents/VoiceAssistant/ui_debug/<VOICE_ASSISTANT_DEBUG_RUN>/`。

用法示例
- 默认（读取 `.env` 的 `VOICE_ASSISTANT_OLLAMA_BASE_URL/VOICE_ASSISTANT_VLM_MODEL`）：
  `python test_scripts/test_ollama_chat_smoke.py`

- 指定模型与 base_url：
  `python test_scripts/test_ollama_chat_smoke.py --base-url http://127.0.0.1:11434 --model qwen3-vl:4b`

注意
- 该脚本不会输出或落盘任何密钥（本地 Ollama 不需要密钥）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _debug_dir() -> Path:
    base = Path.home() / "Documents" / "VoiceAssistant" / "ui_debug"
    run_id = str(os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or "").strip()
    out = base / run_id if run_id else base
    out.mkdir(parents=True, exist_ok=True)
    return out


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ollama chat smoke")
    parser.add_argument("--base-url", type=str, default="", help="默认使用 VOICE_ASSISTANT_OLLAMA_BASE_URL")
    parser.add_argument("--model", type=str, default="", help="默认使用 VOICE_ASSISTANT_VLM_MODEL")
    parser.add_argument("--timeout", type=float, default=8.0, help="HTTP timeout 秒")
    parser.add_argument("--run-id", type=str, default="", help="如果 VOICE_ASSISTANT_DEBUG_RUN 为空，则使用该值")
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()

    if not str(os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or "").strip():
        run_id = str(args.run_id or "").strip() or f"smoke_ollama_{int(time.time() * 1000)}"
        os.environ["VOICE_ASSISTANT_DEBUG_RUN"] = run_id

    base_url = str(args.base_url or os.environ.get("VOICE_ASSISTANT_OLLAMA_BASE_URL") or "http://127.0.0.1:11434").strip()
    model = str(args.model or os.environ.get("VOICE_ASSISTANT_VLM_MODEL") or "qwen3-vl:4b").strip()

    from backend_py.services.ollama_client import OllamaClient

    client = OllamaClient(base_url=base_url, timeout_sec=float(args.timeout))

    messages = [
        {"role": "system", "content": "你是一个本地运行的助手。请用一句中文回复'ok'。"},
        {"role": "user", "content": "请回复 ok"},
    ]

    result = await client.chat(
        model=model,
        messages=messages,
        images_base64=None,
        stream=False,
        keep_alive="2m",
        options={"temperature": 0.0},
    )

    payload: Dict[str, Any] = {
        "runId": str(os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or "").strip(),
        "baseUrl": base_url,
        "model": model,
        "content": str(result.content or ""),
        "raw": result.raw,
    }

    out_path = _debug_dir() / f"ollama_chat_smoke_{int(time.time() * 1000)}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[smoke] runId={payload['runId']}")
    print(f"[smoke] baseUrl={base_url}")
    print(f"[smoke] model={model}")
    print(f"[smoke] out={out_path}")
    print("\n[smoke] content:")
    print(str(result.content or "").strip())

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
