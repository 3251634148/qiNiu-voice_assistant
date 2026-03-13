"""单元测试：主窗口内弹窗窗口挑选（用于企微全局搜索弹窗截取）。

目标：
- 给定主窗口 bounds 与一组窗口元信息，能稳定选出“位于主窗口内、面积较小”的弹窗窗口；
- 避免误选主窗口自身或其他无关窗口。

运行方式:
    cd voice_assistant
    source backend_py/.venv/bin/activate
    python -m pytest test_scripts/test_macos_ui_automation_popup_pick.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _win(
    *,
    owner: str,
    wid: int,
    x: float,
    y: float,
    w: float,
    h: float,
    onscreen: bool = True,
    alpha: float = 1.0,
) -> dict:
    return {
        "kCGWindowOwnerName": owner,
        "kCGWindowNumber": wid,
        "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h},
        "kCGWindowIsOnscreen": 1 if onscreen else 0,
        "kCGWindowAlpha": alpha,
        "kCGWindowLayer": 0,
    }


def test_pick_child_window_from_infos_prefers_popup_in_parent() -> None:
    from backend_py.services.macos_ui_automation import MacOSUIAutomation

    parent_bounds = {"x": 700, "y": 300, "width": 1152, "height": 801}
    parent_area = float(parent_bounds["width"] * parent_bounds["height"])

    windows = [
        # 主窗口（面积接近 parent_area，应被面积上限过滤掉）
        _win(owner="企业微信", wid=100, x=700, y=300, w=1152, h=801),
        # 弹窗（位于主窗口内，面积较小，应被选中）
        _win(owner="企业微信", wid=200, x=820, y=360, w=960, h=640),
        # 太小的浮层（面积过小，应被过滤）
        _win(owner="企业微信", wid=300, x=720, y=320, w=100, h=50),
        # 其他应用窗口（owner 不匹配）
        _win(owner="Finder", wid=400, x=820, y=360, w=960, h=640),
        # 不在主窗口内
        _win(owner="企业微信", wid=500, x=50, y=50, w=900, h=600),
    ]

    picked = MacOSUIAutomation._pick_child_window_from_infos(  # noqa: SLF001
        windows=windows,
        owner_names=["企业微信", "WeCom"],
        parent_bounds=parent_bounds,
        min_area_ratio=0.02,
        max_area_ratio=0.70,
    )
    assert picked is not None
    wid, bounds, owner = picked
    assert owner == "企业微信"
    assert wid == 200
    assert float(bounds.width * bounds.height) < parent_area * 0.70


def test_pick_child_window_returns_none_when_parent_invalid() -> None:
    from backend_py.services.macos_ui_automation import MacOSUIAutomation

    picked = MacOSUIAutomation._pick_child_window_from_infos(  # noqa: SLF001
        windows=[],
        owner_names=["企业微信"],
        parent_bounds={"x": 0, "y": 0, "width": 0, "height": 0},
    )
    assert picked is None

