from __future__ import annotations

import sys
from pathlib import Path

# 让脚本在未安装为 site-packages 的情况下也能导入 `backend_py/`
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend_py.services.tts_service import TTSService


def run_all() -> None:
    svc = TTSService()

    # 1) prompt 必须包含边界标签策略
    prompt = svc._TTS_SYSTEM_PROMPT
    assert "<READ_TEXT>" in prompt
    assert "</READ_TEXT>" in prompt
    assert "禁止添加" in prompt

    # 2) payload 必须包含低随机性参数，且 user content 被包裹标签
    payload = svc._build_payload(text="你好。", voice="Cherry")
    assert payload.get("model") == "qwen3-omni-flash-2025-12-01"
    assert payload.get("temperature") == 0
    assert payload.get("top_p") == 1

    messages = payload.get("messages")
    assert isinstance(messages, list) and len(messages) == 2
    assert messages[1].get("role") == "user"

    content = messages[1].get("content")
    assert isinstance(content, str)
    assert content.startswith("<READ_TEXT>")
    assert content.endswith("</READ_TEXT>")


if __name__ == "__main__":
    run_all()
    print("OK")
