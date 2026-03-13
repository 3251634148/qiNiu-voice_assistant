"""回归测试：企微发送后“草稿回填”护栏（sentinel 判定）。

背景（来自真实 ui_debug 证据，requestId=5f195fc5-fa86-401d-9750-22a178f47937）：
- 全局搜索阶段会把联系人名写入剪贴板；
- 发送阶段若输入框无草稿或剪切失败，旧逻辑会把剪贴板内容误当草稿回填，
  导致发送后输入框里又出现联系人名（多一步键入/粘贴）。

本测试不依赖真实 UI，通过替身 UI + 内存剪贴板验证：
1) 输入框无草稿时，Cmd+X 不应改变剪贴板；sentinel 判定应返回 (draft="", cut_ok=False)
2) 输入框有草稿时，Cmd+X 应把草稿写入剪贴板；sentinel 判定应返回 (draft=草稿, cut_ok=True)

运行方式:
    cd voice_assistant
    source backend_py/.venv/bin/activate
    python -m pytest test_scripts/test_wecom_ui_controller_draft_restore_guard.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class _FakeUI:
    def __init__(self, *, clipboard: Dict[str, str], draft_text: str) -> None:
        self._clipboard = clipboard
        self._draft_text = draft_text
        self._selected_all = False

    async def hotkey(self, key: str, *, modifiers: Any) -> None:  # noqa: ANN401
        # 仅实现本测试需要的 Cmd+A / Cmd+X
        if key == "a":
            self._selected_all = True
            return
        if key == "x":
            if self._selected_all and self._draft_text:
                self._clipboard["value"] = self._draft_text
                self._draft_text = ""
            return


@pytest.mark.anyio
async def test_cut_draft_returns_false_when_no_draft(monkeypatch: Any) -> None:
    from backend_py.services.wecom_ui_controller import WeComUIController

    clipboard = {"value": "罗晨曦"}  # 模拟全局搜索阶段遗留在剪贴板的联系人名

    def _pbcopy(v: str) -> None:
        clipboard["value"] = v

    def _pbpaste() -> str:
        return clipboard["value"]

    monkeypatch.setattr("backend_py.services.wecom_ui_controller.pbcopy", _pbcopy)
    monkeypatch.setattr("backend_py.services.wecom_ui_controller.pbpaste", _pbpaste)

    ctrl = WeComUIController()
    ctrl.ui = _FakeUI(clipboard=clipboard, draft_text="")  # type: ignore[assignment]

    debug_info: Dict[str, Any] = {}
    draft, cut_ok = await ctrl._cut_chat_input_draft_with_sentinel(debug_info=debug_info)
    assert cut_ok is False
    assert draft == ""


@pytest.mark.anyio
async def test_cut_draft_returns_true_when_draft_present(monkeypatch: Any) -> None:
    from backend_py.services.wecom_ui_controller import WeComUIController

    clipboard = {"value": "罗晨曦"}
    draft_text = "待恢复草稿"

    def _pbcopy(v: str) -> None:
        clipboard["value"] = v

    def _pbpaste() -> str:
        return clipboard["value"]

    monkeypatch.setattr("backend_py.services.wecom_ui_controller.pbcopy", _pbcopy)
    monkeypatch.setattr("backend_py.services.wecom_ui_controller.pbpaste", _pbpaste)

    ctrl = WeComUIController()
    ctrl.ui = _FakeUI(clipboard=clipboard, draft_text=draft_text)  # type: ignore[assignment]

    debug_info: Dict[str, Any] = {}
    draft, cut_ok = await ctrl._cut_chat_input_draft_with_sentinel(debug_info=debug_info)
    assert cut_ok is True
    assert draft == draft_text

