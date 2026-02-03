# -*- coding: utf-8 -*-
"""Logic test: random/generic music requests should not be treated as a song query.

用法：
backend_py/.venv/bin/python test_scripts/test_music_intent_random_query.py
"""

from __future__ import annotations

from pathlib import Path

# Ensure project root is importable when running as a script.
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend_py.controllers.conversation_controller import ConversationController  # noqa: E402


def main() -> int:
    random_cases = [
        "随便一首歌",
        "随机听点音乐",
        "来点音乐",
        "都行，给我放首歌",
        "随便放点歌",
        "听点什么",
    ]

    for t in random_cases:
        assert ConversationController._is_random_music_request(t) is True
        assert ConversationController._extract_music_search_query(t) is None

    specific_cases = [
        "我想听周杰伦的告白气球",
        "播放《演员》",
        "来一首 红豆",
        "放 孙燕姿 遇见",
        "我要听林俊杰的修炼爱情",
    ]

    for t in specific_cases:
        assert ConversationController._is_random_music_request(t) is False
        q = ConversationController._extract_music_search_query(t)
        assert isinstance(q, str) and q.strip()

    picked = ConversationController._pick_random_song_query()
    assert isinstance(picked, str) and picked.strip()

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
