from __future__ import annotations

import sys
from pathlib import Path

# 让脚本在未安装为 site-packages 的情况下也能导入 `backend_py/`
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend_py.services.llm_service import LLMService


def run_all() -> None:
    svc = LLMService()
    p = svc.system_prompt

    # 心情/随机歌必须走搜索真实歌名，并禁止误用 favorites_first
    assert "适合我现在心情" in p
    assert "禁止在此场景使用 favorites_first" in p
    assert "你必须自己挑选 1 首真实存在的歌曲" in p

    # 纯文本创作默认直接产出，不要反复确认
    assert "写作/创作类纯文本任务" in p
    assert "默认不要追问" in p
    assert "视为授权：必须直接产出最终结果" in p


if __name__ == "__main__":
    run_all()
    print("OK")
