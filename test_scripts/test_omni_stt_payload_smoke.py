from __future__ import annotations

import sys
from pathlib import Path

# 让脚本在未安装为 site-packages 的情况下也能导入 `backend_py/`
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend_py.services.asr_service import ASRService


def run_all() -> None:
    # 伪造一个最小 WAV 头（不要求可播放，仅用于 MIME 识别路径）
    # RIFF(4) + size(4) + WAVE(4)
    fake_wav = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 200

    svc = ASRService()
    payload, mime, audio_format = svc._build_payload(fake_wav, language="zh-CN")

    assert mime == "audio/wav"
    assert audio_format == "wav"

    assert payload.get("model") == "qwen3-omni-flash-2025-12-01"
    assert payload.get("stream") is False

    messages = payload.get("messages")
    assert isinstance(messages, list) and len(messages) >= 2

    user_msg = messages[1]
    assert user_msg.get("role") == "user"

    content = user_msg.get("content")
    assert isinstance(content, list) and len(content) >= 2

    audio_part = content[0]
    assert audio_part.get("type") == "input_audio"
    input_audio = audio_part.get("input_audio")
    assert isinstance(input_audio, dict)
    assert input_audio.get("format") == "wav"

    data = input_audio.get("data")
    assert isinstance(data, str)
    assert data.startswith("data:audio/wav;base64,")

    text_part = content[1]
    assert text_part.get("type") == "text"
    assert isinstance(text_part.get("text"), str) and text_part.get("text")


if __name__ == "__main__":
    run_all()
    print("OK")
