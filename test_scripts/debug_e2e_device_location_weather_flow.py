#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""端到端证据脚本：验证“今天天气怎么样”能成功触发设备定位。

用途：
- 复现你提供的真实场景：前端/客户端发送文本“今天天气怎么样”
- 确认后端 tool-loop 能正确执行 get_device_location（不再误判 enabled=false）
- 以 ui_debug 产物作为证据：检查是否生成 local_tool_response_get_device_location_*.json

运行方式（建议）：
1) 另起一个后端端口（避免影响你现有 3002）：
   PORT=3003 bash -lc 'source backend_py/.venv/bin/activate && uvicorn backend_py.main:asgi_app --host 127.0.0.1 --port 3003'
2) 运行本脚本：
   source backend_py/.venv/bin/activate
   python test_scripts/debug_e2e_device_location_weather_flow.py --server http://127.0.0.1:3003

注意：
- 本脚本会在 home 的 ui_debug 下读写证据文件：`~/Documents/VoiceAssistant/ui_debug/<requestId>/`
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import socketio


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _ui_debug_dir(request_id: str) -> Path:
    return Path.home() / "Documents" / "VoiceAssistant" / "ui_debug" / str(request_id)


async def _wait_for_file(pattern: str, *, base_dir: Path, timeout_sec: float = 8.0) -> Optional[Path]:
    deadline = time.time() + float(timeout_sec)
    while time.time() < deadline:
        matches = sorted(base_dir.glob(pattern))
        if matches:
            return matches[-1]
        await asyncio.sleep(0.1)
    return None


async def run_once(*, server: str, client_id: str, text: str) -> Dict[str, Any]:
    # python-socketio 的 transports 选项在 connect() 里传入（不同版本对 __init__ 参数不兼容）。
    sio = socketio.AsyncClient()
    result: Dict[str, Any] = {"ok": False}

    request_id = str(uuid.uuid4())

    assistant_q: asyncio.Queue = asyncio.Queue()
    error_q: asyncio.Queue = asyncio.Queue()

    @sio.on("assistant-message")
    async def _on_assistant_message(data: Dict[str, Any]) -> None:
        if (data or {}).get("requestId") == request_id:
            await assistant_q.put(data)

    @sio.on("error")
    async def _on_error(data: Dict[str, Any]) -> None:
        await error_q.put(data)

    await sio.connect(server, transports=["websocket"])

    # register-client：绑定到稳定 clientId，并进入 room client:<clientId>
    await sio.emit("register-client", {"clientId": client_id})

    # 显式打开联网与设备定位（与前端一致）
    await sio.emit("update-network-settings", {"networkAccessEnabled": True})
    await sio.emit("update-device-location", {"deviceLocationEnabled": True})

    await sio.emit("text-command", {"text": text, "requestId": request_id})

    # 等待 assistant-message（或错误）
    msg_task = asyncio.create_task(assistant_q.get())
    err_task = asyncio.create_task(error_q.get())
    done, pending = await asyncio.wait({msg_task, err_task}, timeout=60.0, return_when=asyncio.FIRST_COMPLETED)
    for p in pending:
        p.cancel()

    assistant_msg = None
    err_msg = None
    for d in done:
        v = d.result()
        if isinstance(v, dict) and "message" in v and (v.get("message") or "").strip():
            err_msg = v
        else:
            assistant_msg = v

    await sio.disconnect()

    out_dir = _ui_debug_dir(request_id)
    loc_resp = await _wait_for_file("local_tool_response_get_device_location_*.json", base_dir=out_dir, timeout_sec=10.0)
    loc_err = await _wait_for_file("local_tool_error_get_device_location_*.json", base_dir=out_dir, timeout_sec=1.0)

    result.update(
        {
            "ok": bool(loc_resp),
            "requestId": request_id,
            "clientId": client_id,
            "text": text,
            "assistantMessage": assistant_msg,
            "socketError": err_msg,
            "uiDebugDir": str(out_dir),
            "deviceLocationResponseFile": str(loc_resp) if loc_resp else None,
            "deviceLocationErrorFile": str(loc_err) if loc_err else None,
        }
    )

    # 如果存在定位 response，打印其摘要（证据）
    if loc_resp and loc_resp.exists():
        try:
            obj = json.loads(loc_resp.read_text(encoding="utf-8"))
            payload = obj.get("payload") if isinstance(obj, dict) else None
            if isinstance(payload, dict):
                result["deviceLocationSummary"] = {
                    "ok": payload.get("ok"),
                    "result": (payload.get("result") or {}),
                }
        except Exception:
            pass

    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:3003", help="Socket.IO server base URL")
    ap.add_argument("--clientId", default=f"web_e2e_{uuid.uuid4().hex[:8]}", help="clientId to register")
    ap.add_argument("--text", default="今天天气怎么样", help="text to send")
    args = ap.parse_args()

    # 让脚本在未安装为 site-packages 的情况下也能导入 `backend_py/`（可选）
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    os.environ.setdefault("VOICE_ASSISTANT_DEBUG_RUN", f"e2e_{int(time.time() * 1000)}")

    out = asyncio.run(run_once(server=args.server, client_id=args.clientId, text=args.text))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nui_debug: {out.get('uiDebugDir')}")
    if out.get("deviceLocationResponseFile"):
        print("✅ 已生成 local_tool_response_get_device_location（设备定位已成功执行）")
    else:
        print("❌ 未生成 local_tool_response_get_device_location（请查看 local_tool_error 与 capability 文件）")


if __name__ == "__main__":
    main()

