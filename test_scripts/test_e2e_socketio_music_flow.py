#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""端到端 E2E：启动后端服务并通过 Socket.IO 跑通“前端同款流程”。

覆盖点
- 启动 `backend_py.main:asgi_app`（uvicorn）
- 通过 Socket.IO 发送 `text-command`
- 自动处理 `request-confirmation` -> 发送 `confirm-action`
- 等待 `tool-result`，要求 success

说明
- 默认开启 LLM stub（`VOICE_ASSISTANT_LLM_STUB=1`），避免依赖外部千问 API。
- 该脚本会触发真实 UI 自动化（高风险）；请确保已授予“辅助功能/屏幕录制”权限且酷狗可用。

调试与日志（重要）
- 本脚本会打印项目工作目录 `PROJECT_ROOT`。
- 每条用例都会打印 `requestId`，并输出对应调试产物目录：`~/Documents/VoiceAssistant/ui_debug/<requestId>/`。
- 每条用例跑完后，脚本会自动列出该目录下最新的截图/JSON 产物，并打印 server 日志 tail。
  如果你后续丢失上下文，只要重读本文件头部这一段，就知道：跑完必须去 `ui_debug/<requestId>` 看当次执行到底点了哪里。

用法
- backend_py/.venv/bin/python test_scripts/test_e2e_socketio_music_flow.py
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import socketio


PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def _wait_health(base_url: str, *, timeout_sec: float = 20.0) -> None:
    deadline = time.time() + float(timeout_sec)
    async with httpx.AsyncClient(timeout=2.0) as client:
        last_err: Optional[str] = None
        while time.time() < deadline:
            try:
                resp = await client.get(f"{base_url}/api/health")
                if resp.status_code == 200:
                    return
                last_err = f"HTTP {resp.status_code}: {resp.text[:120]}"
            except Exception as e:
                last_err = str(e)
            await asyncio.sleep(0.25)

    raise RuntimeError(f"服务未就绪：{last_err}")


def _start_server(*, port: int, stdout: Any) -> subprocess.Popen:
    env = os.environ.copy()
    env.setdefault("PORT", str(port))
    env.setdefault("VOICE_ASSISTANT_LLM_STUB", "1")

    python_bin = str(PROJECT_ROOT / "backend_py" / ".venv" / "bin" / "python")
    if not Path(python_bin).exists():
        raise RuntimeError(f"未找到虚拟环境 python：{python_bin}")

    cmd = [
        python_bin,
        "-m",
        "uvicorn",
        "backend_py.main:asgi_app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]

    return subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=stdout,
        stderr=subprocess.STDOUT,
        text=True,
    )


async def _run_case(
    sio: socketio.AsyncClient,
    *,
    text: str,
    timeout_sec: float = 120.0,
) -> Dict[str, Any]:
    request_id = str(uuid.uuid4())

    confirmation_q: asyncio.Queue = asyncio.Queue()
    result_q: asyncio.Queue = asyncio.Queue()

    @sio.on("request-confirmation")
    async def _on_confirmation(data: Dict[str, Any]) -> None:
        await confirmation_q.put(data)

    @sio.on("tool-result")
    async def _on_tool_result(data: Dict[str, Any]) -> None:
        await result_q.put(data)

    await sio.emit("text-command", {"text": text, "requestId": request_id})

    confirmation = await asyncio.wait_for(confirmation_q.get(), timeout=float(timeout_sec))
    confirmation_id = confirmation.get("id")
    if not confirmation_id:
        raise RuntimeError(f"确认消息缺少 id：{confirmation}")

    await sio.emit("confirm-action", {"confirmationId": confirmation_id, "approved": True})

    result = await asyncio.wait_for(result_q.get(), timeout=float(timeout_sec))
    return {"requestId": request_id, "confirmation": confirmation, "toolResult": result}


def _tail_file(file_path: Path, *, limit: int = 120) -> str:
    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""

    lines = text.splitlines()
    return "\n".join(lines[-max(1, int(limit)) :])


def _debug_dir_for_request(request_id: str) -> Path:
    return Path.home() / "Documents" / "VoiceAssistant" / "ui_debug" / str(request_id)


def _summarize_debug_dir(request_id: str, *, limit: int = 16) -> None:
    debug_dir = _debug_dir_for_request(request_id)
    print(f"[debug_dir] {debug_dir}")
    if not debug_dir.exists():
        print("[debug_dir] (missing)")
        return

    files = sorted(debug_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[: max(1, int(limit))]:
        try:
            size = p.stat().st_size
        except Exception:
            size = -1
        print(f"- {p.name} ({size} bytes)")


async def main() -> int:
    port = int(os.getenv("E2E_PORT", "3011"))
    base_url = f"http://127.0.0.1:{port}"

    run_id = str(uuid.uuid4())
    run_dir = PROJECT_ROOT / "e2e_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    server_log_path = run_dir / "server.log"

    print(f"[cwd] {PROJECT_ROOT}")
    print(f"[e2e_run_dir] {run_dir}")
    print(f"[server_log] {server_log_path}")

    log_f = server_log_path.open("w", encoding="utf-8")
    proc = _start_server(port=port, stdout=log_f)
    try:
        await _wait_health(base_url, timeout_sec=25.0)

        sio = socketio.AsyncClient(reconnection=False)
        await sio.connect(base_url)

        cases = [
            "播放 周杰伦 告白气球",
            "我喜欢 第一首",
            "随便来点歌",
        ]

        results = []
        for t in cases:
            print(f"\n[case] {t}")
            r = await _run_case(sio, text=t, timeout_sec=180.0)
            results.append(r)

            request_id = str(r.get("requestId") or "")
            if request_id:
                print(f"[requestId] {request_id}")
                _summarize_debug_dir(request_id)

            tool_result = (r.get("toolResult") or {})
            if tool_result.get("type") != "success":
                print("\n[server log tail]\n" + _tail_file(server_log_path, limit=160))
                raise RuntimeError(f"工具执行失败：{tool_result}")

            # Optional: summarize debug payload (kugou search/play verification).
            result_payload = tool_result.get("result") if isinstance(tool_result.get("result"), dict) else {}
            debug_payload = result_payload.get("debug") if isinstance(result_payload, dict) else None
            if isinstance(debug_payload, dict):
                pb_list = debug_payload.get("playbackCheck") if isinstance(debug_payload.get("playbackCheck"), list) else []
                rp_list = debug_payload.get("resultsPageDetect") if isinstance(debug_payload.get("resultsPageDetect"), list) else []

                if pb_list:
                    pb = pb_list[-1] if isinstance(pb_list[-1], dict) else {}
                    print("\n[playback_check]", {
                        "confirmed": pb.get("confirmed"),
                        "targetSong": pb.get("targetSong"),
                        "matchOk": pb.get("matchOk"),
                        "progressOk": pb.get("progressOk"),
                        "hashProgressOk": pb.get("hashProgressOk"),
                    })

                if rp_list:
                    rp = rp_list[-1] if isinstance(rp_list[-1], dict) else {}
                    print("\n[results_page_detect]", {
                        "ok": rp.get("ok"),
                        "cancel": rp.get("cancel"),
                        "tabHits": rp.get("tabHits"),
                        "tabCount": rp.get("tabCount"),
                        "tabRatio": rp.get("tabRatio"),
                    })

                # Gate (opt-in): ensure the song is actually playing.
                if os.getenv("E2E_ASSERT_PLAYING", "0") == "1" and "播放" in str(t):
                    confirmed = bool(pb_list and isinstance(pb_list[-1], dict) and pb_list[-1].get("confirmed") is True)
                    if not confirmed:
                        print("\n[warning] playback not confirmed; see ui_debug screenshots")
                        raise RuntimeError("播放确认失败：未能从底栏 OCR + 进度推进判定正在播放目标歌曲")

            print("\n[server log tail]\n" + _tail_file(server_log_path, limit=60))

        await sio.disconnect()

        print("\nOK")
        for r in results:
            tool_result = r.get("toolResult") or {}
            print("-", (tool_result.get("toolCall") or {}), tool_result.get("type"))

        return 0
    finally:
        try:
            log_f.flush()
        except Exception:
            pass

        if proc.poll() is None:
            try:
                proc.send_signal(signal.SIGTERM)
            except Exception:
                pass

            try:
                proc.wait(timeout=8)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        try:
            log_f.close()
        except Exception:
            pass

        tail = _tail_file(server_log_path, limit=120)
        if tail:
            print("\n[server log final tail]\n" + tail)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
