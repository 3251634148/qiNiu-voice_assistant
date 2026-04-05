#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""语音助手关键链路性能基准（ASR / LLM / TTS）。

目标
- 生成你论文需要的“实验数据”：ASR 转写延迟、LLM 响应时间、TTS 首包/总耗时。
- 支持对照：LLM_PROVIDER=dashscope 与 LLM_PROVIDER=ollama。

说明
- 本脚本**不走 Socket.IO**，直接调用 `backend_py.services.*`，避免 UI 自动化副作用。
- m4a 输入：**对齐真实链路**，默认把 `m4a/mp4` 转为 16kHz 单声道 wav 再送 ASR；如需保留原始 `m4a`，用 `--keep-m4a`。

输出
- 结果落盘到：`e2e_runs/bench_<runId>/voice_perf_benchmark.json`

用法示例
- DashScope LLM（真实） + ASR + TTS：
  DASHSCOPE_API_KEY=... \
  backend_py/.venv/bin/python test_scripts/test_voice_perf_benchmark.py --audio ./samples/test.m4a --providers dashscope --repeat 3

- DashScope vs Ollama（对照，仅测 LLM）：
  DASHSCOPE_API_KEY=... \
  OLLAMA_BASE_URL=http://localhost:11434/v1 \
  OLLAMA_MODEL=qwen3.5 \
  backend_py/.venv/bin/python test_scripts/test_voice_perf_benchmark.py --providers dashscope,ollama --repeat 5 --skip-asr --skip-tts
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _pct(xs: List[float], p: float) -> Optional[float]:
    if not xs:
        return None
    ys = sorted(xs)
    if len(ys) == 1:
        return float(ys[0])
    k = (len(ys) - 1) * (float(p) / 100.0)
    f = int(k)
    c = min(f + 1, len(ys) - 1)
    if f == c:
        return float(ys[f])
    d0 = ys[f] * (c - k)
    d1 = ys[c] * (k - f)
    return float(d0 + d1)


def _try_convert_m4a_to_wav_bytes(src_path: Path, *, out_dir: Path) -> Tuple[bytes, Dict[str, Any]]:
    meta: Dict[str, Any] = {"converted": False, "method": "", "out": ""}
    out_dir.mkdir(parents=True, exist_ok=True)
    out_wav = out_dir / f"audio_{_now_ms()}.wav"

    afconvert = shutil.which("afconvert")
    if afconvert:
        cmd = [afconvert, "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(src_path), str(out_wav)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0 and out_wav.exists() and out_wav.stat().st_size > 0:
            meta.update({"converted": True, "method": "afconvert", "out": str(out_wav)})
            return (out_wav.read_bytes(), meta)
        meta["afconvertError"] = (proc.stderr or proc.stdout or "")[:400]

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        cmd = [ffmpeg, "-y", "-i", str(src_path), "-ac", "1", "-ar", "16000", str(out_wav)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0 and out_wav.exists() and out_wav.stat().st_size > 0:
            meta.update({"converted": True, "method": "ffmpeg", "out": str(out_wav)})
            return (out_wav.read_bytes(), meta)
        meta["ffmpegError"] = (proc.stderr or proc.stdout or "")[:400]

    raise RuntimeError("m4a 转 wav 失败：未找到可用转换器（afconvert/ffmpeg）或转换失败")


def _load_audio_bytes(audio_path: Path, *, out_dir: Path, m4a_to_wav: bool) -> Tuple[bytes, Dict[str, Any]]:
    ext = audio_path.suffix.lower().lstrip(".")
    if bool(m4a_to_wav) and ext in {"m4a", "mp4"}:
        return _try_convert_m4a_to_wav_bytes(audio_path, out_dir=out_dir)
    return (audio_path.read_bytes(), {"converted": False, "method": "", "out": ""})


def _reload_services_for_provider(provider: str) -> Tuple[Any, Any, Any, Any]:
    """按 provider 重载 settings 与 service 模块。

    原因：`backend_py.config.settings` 在 import 时读取环境变量，并被多个 service 以
    `from backend_py.config import settings` 方式绑定为模块级变量；因此切 provider 必须 reload。

    返回：Settings(module), ASRService class, LLMService class, TTSService class
    """

    os.environ["LLM_PROVIDER"] = str(provider).strip().lower()

    import backend_py.config as cfg  # noqa: E402
    import backend_py.services.asr_service as asr_mod  # noqa: E402
    import backend_py.services.llm_service as llm_mod  # noqa: E402
    import backend_py.services.tts_service as tts_mod  # noqa: E402

    importlib.reload(cfg)
    importlib.reload(asr_mod)
    importlib.reload(llm_mod)
    importlib.reload(tts_mod)

    return (cfg, asr_mod.ASRService, llm_mod.LLMService, tts_mod.TTSService)


@dataclass
class OneRun:
    provider: str
    asr_ms: Optional[int]
    llm_ms: Optional[int]
    tts_ttfb_ms: Optional[int]
    tts_total_ms: Optional[int]
    meta: Dict[str, Any]


async def _bench_once(
    *,
    provider: str,
    audio_bytes: bytes,
    llm_prompt: str,
    tts_text: str,
    skip_asr: bool,
    skip_llm: bool,
    skip_tts: bool,
) -> OneRun:
    # 任何单阶段失败都不应让整次对照实验“全盘无数据”。
    # 我们把异常写进 meta 并返回 None 的耗时字段。
    try:
        cfg, ASRService, LLMService, TTSService = _reload_services_for_provider(provider)
    except Exception as e:
        return OneRun(
            provider=provider,
            asr_ms=None,
            llm_ms=None,
            tts_ttfb_ms=None,
            tts_total_ms=None,
            meta={"provider": provider, "error": f"reload_services_failed: {e}"},
        )

    meta: Dict[str, Any] = {
        "provider": provider,
        "settings": {
            "llm_provider": str(getattr(cfg, "settings", None).llm_provider),
            "ollama_base_url": str(getattr(cfg, "settings", None).ollama_base_url),
            "ollama_model": str(getattr(cfg, "settings", None).ollama_model),
        },
    }

    asr_ms: Optional[int] = None
    llm_ms: Optional[int] = None
    tts_ttfb_ms: Optional[int] = None
    tts_total_ms: Optional[int] = None

    if not skip_asr:
        try:
            asr = ASRService()
            t0 = time.perf_counter()
            text = await asr.transcribe(audio_bytes, language="zh-CN")
            asr_ms = int(round((time.perf_counter() - t0) * 1000.0))
            meta["asrTextLen"] = int(len(str(text or "")))
        except Exception as e:
            meta["asrError"] = str(e)
            asr_ms = None

    if not skip_llm:
        llm = LLMService()
        # 禁用 tools，让 LLM 响应时间更可控/更可重复
        messages = [{"role": "user", "content": str(llm_prompt)}]
        t0 = time.perf_counter()
        resp = await llm.invoke_llm(messages, tools=[], max_tokens=256)
        llm_ms = int(round((time.perf_counter() - t0) * 1000.0))
        meta["llmProvider"] = str(resp.get("provider") or "")
        meta["llmModel"] = str(resp.get("model") or "")
        meta["llmTextLen"] = int(len(str(resp.get("text") or "")))
        meta["llmUsage"] = resp.get("usage")

    if not skip_tts:
        try:
            tts = TTSService()
            first_chunk_ms: Optional[int] = None
            chunk_count = 0
            total_audio_bytes = 0

            async def _on_chunk(wav_bytes: bytes) -> None:
                nonlocal first_chunk_ms, chunk_count, total_audio_bytes
                if first_chunk_ms is None:
                    first_chunk_ms = _now_ms()
                chunk_count += 1
                total_audio_bytes += int(len(wav_bytes or b""))

            t0_wall_ms = _now_ms()
            t0 = time.perf_counter()
            _ = await tts.text_to_speech(
                str(tts_text),
                {"gender": "female", "voice": "Cherry"},
                on_audio_chunk=_on_chunk,
                request_id=f"bench_{provider}_{uuid.uuid4().hex[:8]}",
            )
            tts_total_ms = int(round((time.perf_counter() - t0) * 1000.0))
            if first_chunk_ms is not None:
                tts_ttfb_ms = int(first_chunk_ms - t0_wall_ms)
            meta["ttsChunkCount"] = int(chunk_count)
            meta["ttsTotalAudioBytes"] = int(total_audio_bytes)
        except Exception as e:
            meta["ttsError"] = str(e)
            tts_ttfb_ms = None
            tts_total_ms = None

    return OneRun(
        provider=provider,
        asr_ms=asr_ms,
        llm_ms=llm_ms,
        tts_ttfb_ms=tts_ttfb_ms,
        tts_total_ms=tts_total_ms,
        meta=meta,
    )


def _summarize_runs(runs: List[OneRun]) -> Dict[str, Any]:
    def _series(name: str) -> List[float]:
        out: List[float] = []
        for r in runs:
            v = getattr(r, name)
            if v is None:
                continue
            out.append(float(v))
        return out

    out: Dict[str, Any] = {}
    for key in ["asr_ms", "llm_ms", "tts_ttfb_ms", "tts_total_ms"]:
        xs = _series(key)
        if not xs:
            out[key] = None
            continue
        out[key] = {
            "n": int(len(xs)),
            "mean": float(mean(xs)),
            "p50": _pct(xs, 50),
            "p90": _pct(xs, 90),
            "p95": _pct(xs, 95),
            "min": float(min(xs)),
            "max": float(max(xs)),
        }
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description="ASR/LLM/TTS performance benchmark")
    parser.add_argument("--audio", type=str, default="", help="可选：本地音频文件路径（m4a/wav/webm/mp3）")
    parser.add_argument("--providers", type=str, default="dashscope,ollama", help="以逗号分隔，例如 dashscope,ollama")
    parser.add_argument(
        "--keep-m4a",
        action="store_true",
        help="若输入为 m4a/mp4，默认会转为 16kHz 单声道 wav 以对齐真实录音链路；传该参数则强制保留 m4a 原始 bytes（可能导致 ASR 失败）",
    )
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--skip-asr", action="store_true")
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--skip-tts", action="store_true")
    parser.add_argument("--llm-prompt", type=str, default="请用一句话解释什么是全链路追踪。")
    parser.add_argument("--tts-text", type=str, default="下面开始进行语音合成性能测试。")
    args = parser.parse_args()

    providers = [p.strip().lower() for p in str(args.providers).split(",") if p.strip()]
    if not providers:
        raise RuntimeError("providers 不能为空")

    run_id = str(uuid.uuid4())
    run_dir = PROJECT_ROOT / "e2e_runs" / f"bench_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    audio_bytes = b""
    audio_meta: Dict[str, Any] = {"provided": False}
    if not bool(args.skip_asr):
        if not str(args.audio or "").strip():
            raise RuntimeError("未传 --audio，但未启用 --skip-asr")
        audio_path = Path(str(args.audio)).expanduser().resolve()
        if not audio_path.exists():
            raise RuntimeError(f"音频文件不存在：{audio_path}")
        audio_bytes, conv_meta = _load_audio_bytes(audio_path, out_dir=run_dir / "audio", m4a_to_wav=bool(args.m4a_to_wav))
        audio_meta = {"provided": True, "path": str(audio_path), "bytes": len(audio_bytes), **conv_meta}

    env_info = {
        "timestampMs": _now_ms(),
        "runId": run_id,
        "platform": platform.platform(),
        "python": sys.version,
        "machine": platform.machine(),
        "processor": platform.processor(),
    }

    results: Dict[str, Any] = {"meta": {"env": env_info, "audio": audio_meta, "repeat": int(args.repeat)}, "providers": {}}

    for provider in providers:
        runs: List[OneRun] = []
        for i in range(int(args.repeat)):
            r = await _bench_once(
                provider=provider,
                audio_bytes=audio_bytes,
                llm_prompt=str(args.llm_prompt),
                tts_text=str(args.tts_text),
                skip_asr=bool(args.skip_asr),
                skip_llm=bool(args.skip_llm),
                skip_tts=bool(args.skip_tts),
            )
            runs.append(r)
            # 若本轮出现错误，仍继续后续重复/后续 provider，便于产出完整对照数据。
            # 给 Ollama 轻微喘息，避免 keep-alive/冷启动抖动影响下一次
            await asyncio.sleep(0.05)

        results["providers"][provider] = {
            "summary": _summarize_runs(runs),
            "runs": [
                {
                    "asrMs": rr.asr_ms,
                    "llmMs": rr.llm_ms,
                    "ttsTtfbMs": rr.tts_ttfb_ms,
                    "ttsTotalMs": rr.tts_total_ms,
                    "meta": rr.meta,
                }
                for rr in runs
            ],
        }

    out_path = run_dir / "voice_perf_benchmark.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    raise SystemExit(asyncio.run(main()))
