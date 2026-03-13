"""回归测试：企业微信 OCR-first 工作流的候选选择与噪声过滤。

背景（来自真实 ui_debug 证据）：
- chat_list ROI 过大时，OCR 可能会抽到聊天正文/草稿区域的长句；
- 若候选选择未过滤“正文样式文本”，会导致 directChatPick 误命中，
  进而触发全局搜索兜底并最终命中“未进入目标联系人会话”的防误发护栏。

本文件不依赖真实 UI，只验证纯算法部分：
1) _is_noise_text：能过滤时间戳、编号条目、长句正文等；
2) _best_match_box：在包含噪声/正文干扰的 boxes 中仍能选中目标联系人。

运行方式:
    cd voice_assistant
    source backend_py/.venv/bin/activate
    python -m pytest test_scripts/test_wecom_ui_controller_pick_and_noise.py -v
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@dataclass
class _Box:
    text: str
    confidence: float = 0.9


def test_is_noise_text_filters_sentence_like_text() -> None:
    from backend_py.services.wecom_ui_controller import WeComUIController

    ctrl = WeComUIController()

    assert ctrl._is_noise_text("1. 不提供语音解析以及语") is True
    assert ctrl._is_noise_text("2) 这是一个很长的句子，用于模拟正文。") is True
    assert ctrl._is_noise_text("12:29") is True
    assert ctrl._is_noise_text("草稿") is True
    assert ctrl._is_noise_text("没有找到相关结果") is True
    assert ctrl._is_noise_text("使用 智能搜索 查找记忆模糊和零散分散的内容") is True

    assert ctrl._is_noise_text("罗晨曦") is False
    assert ctrl._is_noise_text("张三") is False


def test_best_match_box_ignores_noise_and_picks_contact() -> None:
    from backend_py.services.wecom_ui_controller import WeComUIController

    ctrl = WeComUIController()
    target = "罗晨曦"

    boxes = [
        _Box(text="1. 不提供语音解析以及语", confidence=0.98),
        _Box(text="这是一个很长的句子，用于模拟正文，避免被当成联系人。", confidence=0.99),
        _Box(text="罗晨曦", confidence=0.55),
        _Box(text="罗晨曦（草稿）", confidence=0.95),
    ]

    pick = ctrl._best_match_box(boxes, target=target, min_conf=0.30)
    assert pick is not None
    assert "罗晨曦" in pick.text
    assert pick.similarity >= 0.78


def test_best_match_box_allows_contact_with_suffix_like_org() -> None:
    """回归：联系人列表常见“姓名@组织”显示，不应被相似度阈值误判。"""

    from backend_py.services.wecom_ui_controller import WeComUIController

    ctrl = WeComUIController()
    target = "罗晨曦"

    boxes = [
        _Box(text="罗晨曦@深圳大学", confidence=0.5),
        _Box(text="2022150153", confidence=0.99),  # 应被过滤为噪声（长数字）
    ]

    pick = ctrl._best_match_box(boxes, target=target, min_conf=0.30)
    assert pick is not None
    assert pick.text == "罗晨曦@深圳大学"
    assert pick.similarity >= 0.78


def test_best_match_box_returns_none_when_target_empty() -> None:
    from backend_py.services.wecom_ui_controller import WeComUIController

    ctrl = WeComUIController()
    pick = ctrl._best_match_box([_Box(text="罗晨曦")], target="", min_conf=0.30)
    assert pick is None

