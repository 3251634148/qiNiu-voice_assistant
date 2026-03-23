from __future__ import annotations

import base64
import sys
from pathlib import Path

# 让脚本在未安装为 site-packages 的情况下也能导入 `backend_py/`
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend_py.services.tts_service import TTSService
from backend_py.utils.wav import create_wav_header


def test_take_base64_decodable_prefix_keeps_remainder() -> None:
    buf = "abcd" * 3 + "xyz"  # 12 + 3
    decodable, rest = TTSService._take_base64_decodable_prefix(buf)
    assert len(decodable) % 4 == 0
    assert decodable + rest == buf


def test_take_base64_decodable_prefix_empty() -> None:
    decodable, rest = TTSService._take_base64_decodable_prefix("abc")
    assert decodable == ""
    assert rest == "abc"


def test_pcm_to_wav_header_roundtrip_smoke() -> None:
    pcm = b"\x00\x01" * 1000
    wav = create_wav_header(len(pcm), sample_rate=24000) + pcm
    assert wav.startswith(b"RIFF")
    assert b"WAVE" in wav[:16]


def test_base64_partial_decode_strategy_smoke() -> None:
    pcm = b"hello_pcm" * 100
    b64 = base64.b64encode(pcm).decode("ascii")

    # 模拟分段到达：每次只提供 5 个字符
    buf = ""
    out = b""
    for i in range(0, len(b64), 5):
        buf += b64[i : i + 5]
        decodable, buf = TTSService._take_base64_decodable_prefix(buf)
        if decodable:
            out += base64.b64decode(decodable)

    if buf:
        padded = buf + ("=" * ((4 - (len(buf) % 4)) % 4))
        out += base64.b64decode(padded)

    assert out == pcm


def run_all() -> None:
    """在未安装 pytest 的环境中也能运行的最小自检。"""

    test_take_base64_decodable_prefix_keeps_remainder()
    test_take_base64_decodable_prefix_empty()
    test_pcm_to_wav_header_roundtrip_smoke()
    test_base64_partial_decode_strategy_smoke()


if __name__ == "__main__":
    run_all()
    print("OK")
