#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""端到端 E2E（语音版）：本地音频文件 -> Socket.IO `voice-input` -> ASR -> LLM ->（可选工具）-> TTS。

覆盖点
- 启动 `backend_py.main:asgi_app`（uvicorn）
- 发送 `voice-input`（与前端一致：binary bytes 直传）
- 监听并记录：
  - `speech-recognized`（ASR 输出文本）
  - `assistant-message`（最终给用户的文字）
  - `request-confirmation` / `confirm-action`（高风险工具确认）
  - `tool-result`（工具执行结果）
  - `audio-chunk`（TTS 音频流，记录首包/完成耗时）

安全默认
- 默认 **不自动确认高风险工具**；如需覆盖“语音→操作→语音”完整链路，请加 `--auto-approve-tools`。

m4a 支持
- 为对齐真实工作链路（热键/硬件语音都是 16kHz mono WAV），脚本默认 `--audio-send-format auto` 会在输入为 `m4a/mp4` 时先转为 **16kHz 单声道 int16 WAV** 再发送。
- 如需对照：可用 `--audio-send-format original` 强制发送原始 `m4a/mp4` bytes（DashScope ASR 可能不支持）。

用法示例
- 纯语音对话（不触发工具）：
  backend_py/.venv/bin/python test_scripts/test_e2e_socketio_voice_flow.py --audio ./samples/hello.m4a

- 完整链路（可能触发 UI 自动化，高风险）：
  backend_py/.venv/bin/python test_scripts/test_e2e_socketio_voice_flow.py --audio ./samples/play_song.m4a --auto-approve-tools

- 对照实验：用固定文本覆盖 ASR（会额外发送一次 text-command）：
  backend_py/.venv/bin/python test_scripts/test_e2e_socketio_voice_flow.py --audio ./samples/hello.m4a --override-text "写一首五言绝句"

输出
- 每次运行会生成 runId 并落盘：
  - `e2e_runs/<runId>/server.log`
  - `e2e_runs/<runId>/voice_flow_timeline.json`
- 若触发工具，后端仍会把 UI 证据落盘到：`~/Documents/VoiceAssistant/ui_debug/<requestId>/`
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import socketio


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class TimelineEvent:
    name: str
    ts_ms: int
    payload: Dict[str, Any]


def _now_ms() -> int:
    return int(time.time() * 1000)


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


def _start_server(*, port: int, stdout: Any, env_overrides: Dict[str, str]) -> subprocess.Popen:
    env = os.environ.copy()
    env.setdefault("PORT", str(port))
    for k, v in (env_overrides or {}).items():
        if v is None:
            continue
        env[str(k)] = str(v)

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


def _tail_file(file_path: Path, *, limit: int = 140) -> str:
    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""

    lines = text.splitlines()
    return "\n".join(lines[-max(1, int(limit)) :])


def _debug_dir_for_request(request_id: str) -> Path:
    return Path.home() / "Documents" / "VoiceAssistant" / "ui_debug" / str(request_id)


def _summarize_debug_dir(request_id: str, *, limit: int = 18) -> List[Dict[str, Any]]:
    debug_dir = _debug_dir_for_request(request_id)
    if not debug_dir.exists():
        return []

    files = sorted(debug_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: List[Dict[str, Any]] = []
    for p in files[: max(1, int(limit))]:
        try:
            size = int(p.stat().st_size)
        except Exception:
            size = -1
        out.append({"name": p.name, "bytes": size})
    return out


def _try_convert_m4a_to_wav_bytes(src_path: Path, *, out_dir: Path) -> Tuple[bytes, Dict[str, Any]]:
    """将 m4a/mp4 转为 wav bytes。

    说明：真实链路送给 ASR 的是 16kHz/mono/int16 WAV。
脚本在 `--audio-send-format auto|wav` 且输入为 m4a/mp4 时，会先转 wav 再发送。
    """

    meta: Dict[str, Any] = {"converted": False, "method": "", "out": ""}

    out_dir.mkdir(parents=True, exist_ok=True)
    out_wav = out_dir / f"audio_{_now_ms()}.wav"

    # 1) afconvert（macOS 自带）
    afconvert = shutil.which("afconvert")
    if afconvert:
        # -f WAVE: 输出 wav
        # -d LEI16: 16-bit little-endian PCM
        cmd = [
            afconvert,
            "-f",
            "WAVE",
            "-d",
            "LEI16",
            str(src_path),
            str(out_wav),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0 and out_wav.exists() and out_wav.stat().st_size > 0:
            meta.update({"converted": True, "method": "afconvert", "out": str(out_wav)})
            return (out_wav.read_bytes(), meta)
        meta["afconvertError"] = (proc.stderr or proc.stdout or "")[:400]

    # 2) ffmpeg（可选）
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(src_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            str(out_wav),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0 and out_wav.exists() and out_wav.stat().st_size > 0:
            meta.update({"converted": True, "method": "ffmpeg", "out": str(out_wav)})
            return (out_wav.read_bytes(), meta)
        meta["ffmpegError"] = (proc.stderr or proc.stdout or "")[:400]

    raise RuntimeError("m4a 转 wav 失败：未找到可用转换器（afconvert/ffmpeg）或转换失败")


def _load_audio_bytes(audio_path: Path, *, out_dir: Path, send_format: str) -> Tuple[bytes, Dict[str, Any]]:
    """读取本地音频并按 send_format 决定是否转码。

    - auto/original：直接发送原始文件 bytes（推荐，避免 m4a→wav 膨胀）
    - wav：若输入是 m4a/mp4，则先转 wav 再发送
    """

    fmt = str(send_format or "auto").strip().lower()
    if fmt not in {"auto", "original", "wav"}:
        fmt = "auto"

    ext = audio_path.suffix.lower().lstrip(".")
    if fmt in {"auto", "wav"} and ext in {"m4a", "mp4"}:
        return _try_convert_m4a_to_wav_bytes(audio_path, out_dir=out_dir)

    return (audio_path.read_bytes(), {"converted": False, "method": "", "out": ""})


async def main() -> int:
    parser = argparse.ArgumentParser(description="E2E voice flow via Socket.IO")
    parser.add_argument("--audio", type=str, required=True, help="本地音频文件路径（支持 m4a/wav/webm/mp3）")
    parser.add_argument(
        "--audio-send-format",
        type=str,
        default="auto",
        choices=["auto", "original", "wav"],
        help="发送给后端的音频格式策略：auto/original=直接发送原始 bytes；wav=若为 m4a/mp4 则先转 wav",
    )
    parser.add_argument(
        "--override-text",
        type=str,
        default="",
        help="可选：覆盖 ASR 文本并额外发送一次 text-command（用于对照实验）",
    )
    parser.add_argument("--port", type=int, default=int(os.getenv("E2E_PORT", "3012")), help="后端监听端口")
    parser.add_argument(
        "--auto-approve-tools",
        action="store_true",
        help="自动同意高风险工具确认（会触发 UI 自动化，请谨慎）",
    )
    parser.add_argument(
        "--llm-provider",
        type=str,
        default="",
        choices=["", "dashscope", "ollama"],
        help="可选：强制设置 LLM_PROVIDER（用于对照实验）",
    )
    parser.add_argument(
        "--llm-stub",
        type=str,
        default="",
        choices=["", "0", "1"],
        help="可选：设置 VOICE_ASSISTANT_LLM_STUB（'' 表示不设置）",
    )
    parser.add_argument("--timeout-sec", type=float, default=180.0)
    args = parser.parse_args()

    audio_path = Path(args.audio).expanduser().resolve()
    if not audio_path.exists():
        raise RuntimeError(f"音频文件不存在：{audio_path}")

    port = int(args.port)
    base_url = f"http://127.0.0.1:{port}"

    run_id = str(uuid.uuid4())
    run_dir = PROJECT_ROOT / "e2e_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    server_log_path = run_dir / "server.log"
    timeline_path = run_dir / "voice_flow_timeline.json"

    print(f"[cwd] {PROJECT_ROOT}")
    print(f"[runId] {run_id}")
    print(f"[e2e_run_dir] {run_dir}")
    print(f"[server_log] {server_log_path}")

    env_overrides: Dict[str, str] = {}
    if str(args.llm_provider or "").strip():
        env_overrides["LLM_PROVIDER"] = str(args.llm_provider).strip()
    if str(args.llm_stub or "").strip() in {"0", "1"}:
        env_overrides["VOICE_ASSISTANT_LLM_STUB"] = str(args.llm_stub).strip()

    log_f = server_log_path.open("w", encoding="utf-8")
    proc = _start_server(port=port, stdout=log_f, env_overrides=env_overrides)

    request_id = str(uuid.uuid4())
    timeline: List[TimelineEvent] = []

    # audio load/convert
    audio_out_dir = run_dir / "audio"
    audio_bytes, convert_meta = _load_audio_bytes(audio_path, out_dir=audio_out_dir, send_format=str(args.audio_send_format))
    timeline.append(
        TimelineEvent(
            name="audio_loaded",
            ts_ms=_now_ms(),
            payload={
                "path": str(audio_path),
                "bytes": len(audio_bytes),
                "sendFormat": str(args.audio_send_format),
                **convert_meta,
            },
        )
    )

    # queues
    recognized_q: asyncio.Queue = asyncio.Queue()
    assistant_q: asyncio.Queue = asyncio.Queue()
    assistant_error_q: asyncio.Queue = asyncio.Queue()
    error_q: asyncio.Queue = asyncio.Queue()
    audio_q: asyncio.Queue = asyncio.Queue()
    confirmation_q: asyncio.Queue = asyncio.Queue()
    tool_result_q: asyncio.Queue = asyncio.Queue()

    disconnected = asyncio.Event()

    sio = socketio.AsyncClient(reconnection=False)

    @sio.on("speech-recognized")
    async def _on_recognized(data: Dict[str, Any]) -> None:
        timeline.append(TimelineEvent(name="speech-recognized", ts_ms=_now_ms(), payload=data if isinstance(data, dict) else {"raw": str(data)}))
        await recognized_q.put(data)

    @sio.on("assistant-message")
    async def _on_assistant_message(data: Dict[str, Any]) -> None:
        payload = data if isinstance(data, dict) else {"raw": str(data)}
        timeline.append(TimelineEvent(name="assistant-message", ts_ms=_now_ms(), payload=payload))
        await assistant_q.put(payload)
        if isinstance(payload, dict) and payload.get("type") == "error":
            await assistant_error_q.put(payload)

    @sio.on("audio-chunk")
    async def _on_audio_chunk(data: Dict[str, Any]) -> None:
        # data.audioData 是 bytes；这里只记录长度，避免 timeline 过大
        payload: Dict[str, Any] = {}
        if isinstance(data, dict):
            payload = dict(data)
            ad = payload.get("audioData")
            if isinstance(ad, (bytes, bytearray)):
                payload["audioBytes"] = len(ad)
                payload.pop("audioData", None)
        timeline.append(TimelineEvent(name="audio-chunk", ts_ms=_now_ms(), payload=payload))
        await audio_q.put(data)

    @sio.on("request-confirmation")
    async def _on_confirmation(data: Dict[str, Any]) -> None:
        timeline.append(TimelineEvent(name="request-confirmation", ts_ms=_now_ms(), payload=data if isinstance(data, dict) else {"raw": str(data)}))
        await confirmation_q.put(data)

    @sio.on("tool-result")
    async def _on_tool_result(data: Dict[str, Any]) -> None:
        timeline.append(TimelineEvent(name="tool-result", ts_ms=_now_ms(), payload=data if isinstance(data, dict) else {"raw": str(data)}))
        await tool_result_q.put(data)

    @sio.on("error")
    async def _on_error(data: Any) -> None:
        payload = data if isinstance(data, dict) else {"raw": str(data)}
        timeline.append(TimelineEvent(name="error", ts_ms=_now_ms(), payload=payload))
        await error_q.put(payload)

    @sio.event
    async def disconnect() -> None:  # type: ignore[override]
        # AsyncClient 断开
        timeline.append(TimelineEvent(name="socketio:disconnect", ts_ms=_now_ms(), payload={"connected": False}))
        disconnected.set()

    @sio.event
    async def connect_error(data: Any) -> None:  # noqa: ANN401
        payload = data if isinstance(data, dict) else {"raw": str(data)}
        timeline.append(TimelineEvent(name="socketio:connect_error", ts_ms=_now_ms(), payload=payload))
        disconnected.set()

    try:
        await _wait_health(base_url, timeout_sec=25.0)
        await sio.connect(base_url)

        t_send_ms = _now_ms()
        timeline.append(TimelineEvent(name="voice-input:send", ts_ms=t_send_ms, payload={"requestId": request_id, "language": "zh-CN", "bytes": len(audio_bytes)}))

        await sio.emit(
            "voice-input",
            {
                "audioData": audio_bytes,
                "language": "zh-CN",
                "requestId": request_id,
            },
        )

        # 1) 等 ASR / error / disconnect（避免断开后“假卡死直到超时”）
        t_rec = asyncio.create_task(recognized_q.get())
        t_err = asyncio.create_task(assistant_error_q.get())
        t_disc = asyncio.create_task(disconnected.wait())
        done, pending = await asyncio.wait({t_rec, t_err, t_disc}, timeout=float(args.timeout_sec), return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()

        if not done:
            raise TimeoutError("等待 ASR 结果超时（未收到 speech-recognized / error / disconnect）")

        if t_disc in done:
            raise RuntimeError("Socket.IO 连接已断开，未收到 ASR 结果（请检查 server.log 是否有包体过大/异常）")

        if t_err in done:
            err_msg = t_err.result() if not isinstance(t_err.result(), Exception) else {"raw": str(t_err.result())}
            raise RuntimeError(f"后端返回错误：{(err_msg or {}).get('content') or err_msg}")

        recognized = t_rec.result()
        rec_text = str((recognized or {}).get("text") or "").strip()

        # 2) 可选：对照实验
        # 说明：后端 handle_voice_input 已经会用 ASR 文本触发 handle_text_command。
        # 若你提供 --override-text，这里会额外 emit 一次 text-command（相同 requestId），用于对照/回归。
        override_text = str(getattr(args, "override_text", "") or "").strip()
        if override_text:
            timeline.append(
                TimelineEvent(
                    name="text-command:send",
                    ts_ms=_now_ms(),
                    payload={"requestId": request_id, "text": override_text},
                )
            )
            await sio.emit("text-command", {"text": override_text, "requestId": request_id})

        # 3) 处理确认/工具（可选）+ 等待 audio complete
        first_audio_ms: Optional[int] = None
        audio_complete_ms: Optional[int] = None

        tool_result: Optional[Dict[str, Any]] = None
        assistant_final: Optional[Dict[str, Any]] = None

        # 我们用一个 loop 等多个事件，直到音频 complete 或超时
        deadline = time.time() + float(args.timeout_sec)
        while time.time() < deadline:
            timeout = max(0.1, min(1.0, deadline - time.time()))

            # 优先处理确认
            try:
                conf = await asyncio.wait_for(confirmation_q.get(), timeout=timeout)
                if bool(args.auto_approve_tools):
                    confirmation_id = (conf or {}).get("id") if isinstance(conf, dict) else None
                    if not confirmation_id:
                        raise RuntimeError(f"确认消息缺少 id：{conf}")
                    timeline.append(
                        TimelineEvent(
                            name="confirm-action:send",
                            ts_ms=_now_ms(),
                            payload={"confirmationId": confirmation_id, "approved": True},
                        )
                    )
                    await sio.emit("confirm-action", {"confirmationId": confirmation_id, "approved": True})
                else:
                    # 未开启 auto approve 时，遇到确认就停止等待（避免脚本卡住）
                    raise RuntimeError("收到了高风险工具确认，但未启用 --auto-approve-tools。")
                continue
            except asyncio.TimeoutError:
                pass

            # tool-result（如果有）
            try:
                tr = await asyncio.wait_for(tool_result_q.get(), timeout=0.01)
                if isinstance(tr, dict):
                    tool_result = tr
                continue
            except asyncio.TimeoutError:
                pass

            # assistant-message（记录最终文本）
            try:
                am = await asyncio.wait_for(assistant_q.get(), timeout=0.01)
                if isinstance(am, dict) and am.get("type") == "assistant":
                    assistant_final = am
                continue
            except asyncio.TimeoutError:
                pass

            # audio-chunk
            try:
                ac = await asyncio.wait_for(audio_q.get(), timeout=0.01)
                if isinstance(ac, dict):
                    if first_audio_ms is None:
                        first_audio_ms = _now_ms()
                    if bool(ac.get("isComplete")):
                        audio_complete_ms = _now_ms()
                        break
                continue
            except asyncio.TimeoutError:
                pass

        # 汇总
        summary: Dict[str, Any] = {
            "runId": run_id,
            "requestId": request_id,
            "audio": {"path": str(audio_path), "bytes": len(audio_bytes), **convert_meta},
            "recognized": {"text": rec_text, "length": len(rec_text)},
            "timing": {
                "sentAtMs": int(t_send_ms),
                "firstAudioAtMs": int(first_audio_ms) if first_audio_ms is not None else None,
                "audioCompleteAtMs": int(audio_complete_ms) if audio_complete_ms is not None else None,
                "asrToFirstAudioMs": int(first_audio_ms - t_send_ms) if (first_audio_ms and t_send_ms) else None,
                "asrToCompleteMs": int(audio_complete_ms - t_send_ms) if (audio_complete_ms and t_send_ms) else None,
            },
            "assistantFinal": assistant_final,
            "toolResult": tool_result,
            "uiDebug": _summarize_debug_dir(request_id),
        }

        out_obj = {
            "summary": summary,
            "timeline": [
                {"name": e.name, "tsMs": int(e.ts_ms), "payload": e.payload}
                for e in timeline
            ],
        }
        timeline_path.write_text(json.dumps(out_obj, ensure_ascii=False, indent=2), encoding="utf-8")

        print(json.dumps({"ok": True, "out": str(timeline_path), "requestId": request_id}, ensure_ascii=False))

        # 若 tool-result 明确失败，返回非 0 方便 CI/回归
        if isinstance(tool_result, dict) and tool_result.get("type") not in {None, "success"}:
            print("\n[server log tail]\n" + _tail_file(server_log_path, limit=180))
            return 2

        return 0

    except Exception as e:
        # 异常也要落盘时间线，避免“只看到超时但没有证据”。
        out_obj = {
            "summary": {
                "runId": run_id,
                "requestId": request_id,
                "ok": False,
                "error": str(e),
                "audio": {"path": str(audio_path), "bytes": len(audio_bytes), **convert_meta},
                "serverLogTail": _tail_file(server_log_path, limit=180),
                "uiDebug": _summarize_debug_dir(request_id),
            },
            "timeline": [{"name": ev.name, "tsMs": int(ev.ts_ms), "payload": ev.payload} for ev in timeline],
        }
        try:
            timeline_path.write_text(json.dumps(out_obj, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        print(json.dumps({"ok": False, "error": str(e), "out": str(timeline_path), "requestId": request_id}, ensure_ascii=False))
        print("\n[server log tail]\n" + _tail_file(server_log_path, limit=180))
        return 3

    finally:
        try:
            await sio.disconnect()
        except Exception:
            pass

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
    # 为了避免在某些 IDE 环境下找不到项目根目录，强制把仓库根目录加入 sys.path
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    raise SystemExit(asyncio.run(main()))
