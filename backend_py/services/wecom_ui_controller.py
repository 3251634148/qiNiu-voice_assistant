from __future__ import annotations

import asyncio
import difflib
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

from backend_py.services.macos_ui_automation import MacOSUIAutomation
from backend_py.services.ui_workflow_utils import (
    dump_json,
    now_ms,
    pbcopy,
    pbpaste,
    roi_from_env,
    text_digest,
)


WECOM_APP_NAMES: list[str] = ["企业微信", "WeCom", "WeComApp"]


@dataclass(frozen=True)
class WeComPick:
    text: str
    confidence: float
    similarity: float
    score: float


class WeComUIController:
    """企业微信 macOS App 的 OCR-first 自动化控制。

    目标：
    - 优先从"消息"页左侧会话列表直接进入目标联系人会话（更快、更稳）。
    - 若直达失败，再走"全局搜索 -> 联系人 -> 点击目标联系人"。
    - 发送前强校验：必须确认当前会话标题命中目标联系人，否则中止（防误发）。

    约束：
    - 任何需要点击/键入的动作都属于高风险，外层应走确认流程。
    - ui_debug 只记录必要证据，不落盘用户剪贴板/草稿明文。
    """

    def __init__(self) -> None:
        self.ui = MacOSUIAutomation()

    @staticmethod
    def _norm_text(text: str) -> str:
        v = str(text or "")
        v = re.sub(r"\s+", "", v)
        v = v.replace("\uffff", "").replace("\ufffd", "")
        return v.strip().lower()

    async def _open_and_frontmost(self, *, debug: Dict[str, Any]) -> str:
        opened = None
        for name in WECOM_APP_NAMES:
            subprocess.run(["open", "-a", name], capture_output=True, text=True, check=False)
            opened = name
            await asyncio.sleep(0.08)

        for _ in range(3):
            try:
                await self.ui.activate_app(WECOM_APP_NAMES[0])
            except Exception:
                pass
            try:
                await self.ui.set_process_frontmost(WECOM_APP_NAMES[0])
            except Exception:
                pass
            await asyncio.sleep(0.12)

            try:
                frontmost = await self.ui.get_frontmost_process_name()
            except Exception:
                frontmost = ""

            if str(frontmost).strip() in set(WECOM_APP_NAMES):
                debug.setdefault("frontmost", []).append({"ok": True, "process": frontmost})
                return str(frontmost).strip()

        debug.setdefault("frontmost", []).append({"ok": False, "opened": opened})
        return str(opened or WECOM_APP_NAMES[0])

    async def search_contact_and_send(
        self,
        *,
        contact_name: str,
        message: str,
        debug: bool = False,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        await self.ui.ensure_accessibility_ready()

        contact = str(contact_name or "").strip()
        msg = str(message or "")
        if not contact:
            raise RuntimeError("contactName 不能为空")
        if not msg.strip():
            raise RuntimeError("message 不能为空")

        debug_info: Dict[str, Any] = {
            "mode": "wecom_ocr_workflow_v2",
            "contactName": contact,
            "messageDigest": text_digest(msg).__dict__,
            "dryRun": bool(dry_run),
        }

        window_w = int(os.environ.get("VOICE_ASSISTANT_APP_WINDOW_W", "1152") or 1152)
        window_h = int(os.environ.get("VOICE_ASSISTANT_APP_WINDOW_H", "801") or 801)

        process_name = await self._open_and_frontmost(debug=debug_info)
        try:
            norm = await self.ui.normalize_process_window(
                process_name=process_name,
                width=window_w,
                height=window_h,
                center_main_screen=True,
            )
            debug_info["windowNormalize"] = norm
        except Exception as e:
            raise RuntimeError(f"企业微信窗口归一化失败：{e}")

        await asyncio.sleep(0.20)

        _json_dumped = False

        def _dump_json_once(tag: str = "wecom_workflow_debug") -> None:
            nonlocal _json_dumped
            if _json_dumped:
                return
            _json_dumped = True
            dump_json(tag, {"timestampMs": now_ms(), "debug": debug_info})

        try:
            return await self._search_contact_and_send_inner(
                contact=contact,
                msg=msg,
                dry_run=dry_run,
                debug=debug,
                debug_info=debug_info,
                _dump_json_once=_dump_json_once,
            )
        except Exception:
            debug_info["error"] = True
            _dump_json_once("wecom_workflow_debug_error")
            raise

    async def _search_contact_and_send_inner(
        self,
        *,
        contact: str,
        msg: str,
        dry_run: bool,
        debug: bool,
        debug_info: Dict[str, Any],
        _dump_json_once: Any,
    ) -> Dict[str, Any]:

        rois = {
            # 左侧主导航（消息/邮件/文档/日程/通讯录…）
            "left_nav": roi_from_env("WECOM_LEFT_NAV_ROI", default=(0.0, 0.10, 0.14, 0.86)),
            # 左侧会话列表（用于"能直达就直达"，避免走全局搜索）
            "chat_list": roi_from_env("WECOM_CHAT_LIST_ROI", default=(0.14, 0.16, 0.28, 0.78)),
            # 聊天窗口顶部标题区（发送前校验，防误发）
            # 实测标题文字在 y≈0.01~0.03、x≈0.33~0.37；旧值 (0.42,0.10,...) 偏移到聊天区导致 OCR 失败
            "chat_header": roi_from_env("WECOM_CHAT_HEADER_ROI", default=(0.33, 0.01, 0.35, 0.08)),
            # Shift+Cmd+F 全局搜索弹窗中的搜索区域
            "search_popup": roi_from_env("WECOM_SEARCH_POPUP_ROI", default=(0.02, 0.02, 0.55, 0.20)),
            # 全局搜索弹窗顶部 tabs（联系人/群聊/聊天记录…）
            "global_tabs": roi_from_env("WECOM_GLOBAL_TABS_ROI", default=(0.12, 0.10, 0.70, 0.12)),
            # 全局搜索结果区
            "results_list": roi_from_env("WECOM_RESULTS_LIST_ROI", default=(0.12, 0.22, 0.86, 0.70)),
        }
        debug_info["rois"] = rois

        async def _ocr(
            cap: Dict[str, Any],
            *,
            roi: tuple[float, float, float, float],
            custom_words: Sequence[str],
        ) -> list[Any]:
            path = str(cap.get("screenshotPath") or "")
            if not path:
                return []
            return await self.ui.ocr_screenshot_advanced(
                path,
                roi=roi,
                scale=3.2,
                grayscale=True,
                accurate=False,
                custom_words=list(custom_words),
            )

        def _to_screen(cap: Dict[str, Any], *, image_x: float, image_y: float) -> tuple[float, float]:
            sx, sy = self.ui._to_screen_point_from_window_image_point(  # noqa: SLF001
                float(image_x),
                float(image_y),
                window_bounds=cap.get("windowBounds") or {},
                image_size=cap.get("imageSize") or {},
            )
            return (float(sx), float(sy))

        async def _click_box_center(cap: Dict[str, Any], box: Any, *, step: str, clicks: int = 1) -> None:
            try:
                cx, cy = box.center()
            except Exception:
                cx = float(getattr(box, "x", 0.0) or 0.0) + float(getattr(box, "width", 0.0) or 0.0) / 2.0
                cy = float(getattr(box, "y", 0.0) or 0.0) + float(getattr(box, "height", 0.0) or 0.0) / 2.0

            sx, sy = _to_screen(cap, image_x=float(cx), image_y=float(cy))
            debug_info.setdefault("clicks", []).append(
                {
                    "step": str(step),
                    "imagePoint": {"x": float(cx), "y": float(cy)},
                    "screenPoint": {"x": float(sx), "y": float(sy)},
                    "anchor": str(getattr(box, "text", "") or ""),
                }
            )

            if not dry_run:
                await self.ui.click_at(float(sx), float(sy), clicks=int(clicks))

        def _is_noise_text(text: str) -> bool:
            t = self._norm_text(text)
            if not t:
                return True
            if re.fullmatch(r"\d{1,2}:\d{2}", t):
                return True
            if "草稿" in t or "图片" in t or "文件" in t:
                return True
            return False

        def _best_match_box(boxes: Sequence[Any], *, target: str, min_conf: float) -> Optional[WeComPick]:
            t_norm = self._norm_text(target)
            if not t_norm:
                return None

            best: Optional[WeComPick] = None
            for b in boxes:
                conf = float(getattr(b, "confidence", 0.0) or 0.0)
                if conf < float(min_conf):
                    continue

                txt = str(getattr(b, "text", "") or "").strip()
                if _is_noise_text(txt):
                    continue

                n = self._norm_text(txt)
                sim = difflib.SequenceMatcher(None, t_norm, n).ratio()
                score = float(sim) + min(0.15, len(n) * 0.002)
                cand = WeComPick(text=txt, confidence=conf, similarity=float(sim), score=float(score))
                if best is None or (cand.score, cand.confidence) > (best.score, best.confidence):
                    best = cand
            return best

        def _has_any(boxes: Sequence[Any], keyword: str) -> bool:
            k = self._norm_text(keyword)
            if not k:
                return False
            for b in boxes:
                t = self._norm_text(str(getattr(b, "text", "") or ""))
                if k in t:
                    return True
            return False

        async def _verify_chat_header_or_raise() -> None:
            cap_hdr = await self.ui.screenshot_window(owner_names=WECOM_APP_NAMES, tag="wecom_chat_header_verify")
            debug_info.setdefault("captures", []).append({"step": "chat_header_verify", "capture": cap_hdr})

            hdr_boxes = await _ocr(cap_hdr, roi=rois["chat_header"], custom_words=[contact])
            debug_info["chatHeaderPreview"] = [str(getattr(b, "text", "") or "") for b in hdr_boxes[:10]]

            if _has_any(hdr_boxes, contact):
                return

            pick = _best_match_box(hdr_boxes, target=contact, min_conf=0.30)
            debug_info["chatHeaderPick"] = pick.__dict__ if pick else None
            if pick is None or pick.similarity < 0.78:
                raise RuntimeError("当前会话标题未命中目标联系人，已中止发送以避免误发")

        async def _verify_chat_header_with_retries() -> None:
            """等待会话真正切换完成后再做标题强校验。

            说明：
            - 真实执行里，点击联系人后 UI 可能短暂处于 loading/切换中，导致标题区 OCR 读取不到目标联系人。
            - 这里通过"截图 + OCR 校验"做有限次重试，并在中途尝试从左侧会话列表再次点入目标会话。
            - **不会降低防误发护栏**：最终仍以标题区命中目标联系人为准，否则中止。
            """

            last_error: Optional[str] = None
            max_attempts = 7
            for attempt in range(max_attempts):
                try:
                    await _verify_chat_header_or_raise()
                    debug_info["chatHeaderVerified"] = {"ok": True, "attempt": int(attempt)}
                    return
                except RuntimeError as e:
                    last_error = str(e)
                    debug_info.setdefault("chatHeaderVerifyRetries", []).append(
                        {"attempt": int(attempt), "error": last_error}
                    )

                    if "标题未命中" not in last_error:
                        raise

                    await asyncio.sleep(0.35 + 0.25 * float(attempt))

                    # 尝试从左侧会话列表再点一次（有时搜索结果点击未真正进入会话）
                    if attempt in {1, 3, 5}:
                        cap_retry = await self.ui.screenshot_window(
                            owner_names=WECOM_APP_NAMES,
                            tag=f"wecom_chat_reopen_retry_{attempt}",
                        )
                        debug_info.setdefault("captures", []).append(
                            {"step": f"chat_reopen_retry_{attempt}", "capture": cap_retry}
                        )

                        retry_boxes = await _ocr(cap_retry, roi=rois["chat_list"], custom_words=[contact])
                        retry_pick = _best_match_box(retry_boxes, target=contact, min_conf=0.35)
                        debug_info.setdefault("retryChatPick", []).append(
                            retry_pick.__dict__ if retry_pick else None
                        )
                        if retry_pick is None or retry_pick.similarity < 0.78:
                            continue

                        for b in retry_boxes:
                            txt = str(getattr(b, "text", "") or "").strip()
                            if self._norm_text(txt) == self._norm_text(retry_pick.text):
                                await _click_box_center(
                                    cap_retry,
                                    b,
                                    step=f"reopen_chat_from_chat_list_{attempt}",
                                )
                                await asyncio.sleep(0.35)
                                break

            debug_info["chatHeaderVerified"] = {"ok": False, "attempt": int(max_attempts - 1)}
            raise RuntimeError(f"{last_error or '当前会话标题未命中目标联系人'}（已重试 {max_attempts} 次）")

        # 记录初始截图
        cap0 = await self.ui.screenshot_window(owner_names=WECOM_APP_NAMES, tag="wecom_flow_init")
        debug_info.setdefault("captures", []).append({"step": "init", "capture": cap0})

        # 1) 进入"消息"页
        if not dry_run:
            try:
                await self.ui.hotkey("1", modifiers=["command down"])
            except Exception as e:
                debug_info.setdefault("warnings", []).append(f"切换消息 tab 快捷键失败（忽略）：{e}")
            # Cmd+1 已完成消息 tab 切换，无需再 OCR+点击"消息"按钮（冗余点击会触发聊天列表刷新/滚动）
            await asyncio.sleep(0.35)

        cap1 = await self.ui.screenshot_window(owner_names=WECOM_APP_NAMES, tag="wecom_after_go_messages")
        debug_info.setdefault("captures", []).append({"step": "after_go_messages", "capture": cap1})

        # 2) 优先左侧会话列表直达
        opened_chat = False
        opened_by = ""

        cap_list = await self.ui.screenshot_window(owner_names=WECOM_APP_NAMES, tag="wecom_chat_list_probe")
        debug_info.setdefault("captures", []).append({"step": "chat_list_probe", "capture": cap_list})

        list_boxes = await _ocr(cap_list, roi=rois["chat_list"], custom_words=[contact])
        direct_pick = _best_match_box(list_boxes, target=contact, min_conf=0.35)
        debug_info["directChatPick"] = direct_pick.__dict__ if direct_pick else None

        if (not dry_run) and direct_pick is not None and direct_pick.similarity >= 0.78:
            for b in list_boxes:
                txt = str(getattr(b, "text", "") or "").strip()
                if self._norm_text(txt) == self._norm_text(direct_pick.text):
                    await _click_box_center(cap_list, b, step="open_chat_from_chat_list")
                    await asyncio.sleep(0.20)
                    opened_chat = True
                    opened_by = "chat_list"
                    break

        def _pick_search_anchor(boxes: Sequence[Any]) -> Optional[Any]:
            best_box: Optional[Any] = None
            best_score = -1.0

            for b in boxes:
                txt = str(getattr(b, "text", "") or "")
                t = self._norm_text(txt)
                if "搜索" not in t:
                    continue

                conf = float(getattr(b, "confidence", 0.0) or 0.0)
                y = float(getattr(b, "y", 0.0) or 0.0)

                exact_bonus = 0.8 if t == "搜索" else 0.3
                top_bonus = max(0.0, 0.5 - min(0.5, y / 600.0))
                score = float(conf) + float(exact_bonus) + float(top_bonus)
                if score > best_score:
                    best_score = score
                    best_box = b

            return best_box

        # dry-run：只采集证据与决策，不做任何点击/键入；避免因 UI 版本差异导致抛错
        if dry_run:
            cap_plan = await self.ui.screenshot_window(owner_names=WECOM_APP_NAMES, tag="wecom_dry_run_plan")
            debug_info.setdefault("captures", []).append({"step": "dry_run_plan", "capture": cap_plan})

            search_boxes = await _ocr(cap_plan, roi=rois["search_popup"], custom_words=["搜索", contact])
            debug_info["dryRunSearchPreview"] = [str(getattr(b, "text", "") or "") for b in search_boxes[:12]]

            debug_info["dryRunPlan"] = {
                "prefer": "chat_list" if (direct_pick is not None and direct_pick.similarity >= 0.78) else "cmd_f_search",
                "note": "真实执行将先 Cmd+F 聚焦顶部搜索框，再输入联系人并回车触发搜索",
            }

            _dump_json_once()
            return {
                "message": f"(dry-run) 将在企业微信搜索并进入 {contact} 会话，然后发送消息",
                "debug": debug_info if debug else None,
            }

        # 3) Shift+Cmd+F 全局搜索弹窗兜底 —— 纯键盘导航策略
        # 原因：screenshot_window 按面积选最大窗口（主窗口），无法截取全局搜索弹窗（独立小窗口），
        # 因此放弃"截图+OCR 在弹窗中操作"，改为纯键盘：输入 → 等待 → Return 选中第一个结果。
        if not opened_chat:
            for attempt in range(3):
                # A. 先用 Escape 关闭可能残留的弹窗/面板（key_code 53 = Escape 键码）
                await self.ui.key_code(53)
                await asyncio.sleep(0.3)

                # B. 触发全局搜索弹窗（Shift+Cmd+F）
                await self.ui.hotkey("f", modifiers=["shift down", "command down"])
                await asyncio.sleep(0.8)  # 等待弹窗动画完成

                debug_info.setdefault("globalSearchKeyboard", []).append(
                    {"attempt": int(attempt), "step": "opened_popup"}
                )

                # C. 弹窗搜索框默认聚焦，清空残留后输入联系人名字
                try:
                    await self.ui.hotkey("a", modifiers=["command down"])
                    await self.ui.key_code(51)  # Delete 清空
                except Exception as e:
                    debug_info.setdefault("warnings", []).append(f"清空全局搜索框失败（忽略）：{e}")

                pbcopy(contact)
                await asyncio.sleep(0.05)
                await self.ui.hotkey("v", modifiers=["command down"])
                await asyncio.sleep(1.5)  # 等待搜索结果加载（需要足够时间）

                debug_info["globalSearchKeyboard"][-1]["step"] = "input_done"

                # D. 按 Return 选中搜索结果中的第一个联系人
                # 企微全局搜索弹窗中，默认第一个结果已高亮，按 Return 即可进入该联系人会话
                await self.ui.key_code(36)  # Return
                await asyncio.sleep(0.5)  # 等待会话切换

                debug_info["globalSearchKeyboard"][-1]["step"] = "return_pressed"

                # E. 用 Escape 关闭全局搜索弹窗残留（确保焦点回到聊天区域）
                await self.ui.key_code(53)  # Escape
                await asyncio.sleep(0.3)

                debug_info["globalSearchKeyboard"][-1]["step"] = "popup_closed"

                # F. 截图验证：检查主窗口的聊天标题区是否已切换到目标联系人
                cap_gs_verify = await self.ui.screenshot_window(
                    owner_names=WECOM_APP_NAMES,
                    tag=f"wecom_global_search_verify_{attempt}",
                )
                debug_info.setdefault("captures", []).append(
                    {"step": f"global_search_verify_{attempt}", "capture": cap_gs_verify}
                )

                hdr_boxes = await _ocr(
                    cap_gs_verify,
                    roi=rois["chat_header"],
                    custom_words=[contact],
                )
                debug_info["globalSearchKeyboard"][-1]["headerPreview"] = [
                    str(getattr(b, "text", "") or "") for b in hdr_boxes[:10]
                ]

                if _has_any(hdr_boxes, contact):
                    opened_chat = True
                    opened_by = "global_search_keyboard"
                    debug_info["globalSearchKeyboard"][-1]["verified"] = True
                    break

                hdr_pick = _best_match_box(hdr_boxes, target=contact, min_conf=0.30)
                debug_info["globalSearchKeyboard"][-1]["headerPick"] = (
                    hdr_pick.__dict__ if hdr_pick else None
                )
                if hdr_pick is not None and hdr_pick.similarity >= 0.70:
                    opened_chat = True
                    opened_by = "global_search_keyboard"
                    debug_info["globalSearchKeyboard"][-1]["verified"] = True
                    break

                debug_info["globalSearchKeyboard"][-1]["verified"] = False
                # 重试前等待
                await asyncio.sleep(0.3)

        debug_info["openedChat"] = {"ok": bool(opened_chat), "by": opened_by}
        if not opened_chat:
            raise RuntimeError("未能进入目标联系人会话，已中止以避免误发")

        # 4) 发送前强校验会话标题（增加稳定等待与重试，避免 UI 切换中误判）
        await _verify_chat_header_with_retries()

        if dry_run:
            _dump_json_once()
            return {"message": f"(dry-run) 将给 {contact} 发送消息", "debug": debug_info if debug else None}

        # 5) 发送消息前：点击聊天输入区域确保焦点不在搜索框
        cap_before_send = await self.ui.screenshot_window(
            owner_names=WECOM_APP_NAMES, tag="wecom_before_send",
        )
        debug_info.setdefault("captures", []).append({"step": "before_send_focus", "capture": cap_before_send})
        iw_send = int(cap_before_send.get("imageWidth", 0) or 0)
        ih_send = int(cap_before_send.get("imageHeight", 0) or 0)
        if iw_send > 0 and ih_send > 0:
            # 聊天输入框大约在窗口右侧偏中下：x≈0.65, y≈0.85
            input_x = int(iw_send * 0.65)
            input_y = int(ih_send * 0.85)
            await self.ui.click(input_x, input_y)
            await asyncio.sleep(0.15)

        # 6) 发送消息，并恢复草稿（不落盘明文）
        original_clipboard = pbpaste()
        debug_info["clipboardDigestBefore"] = text_digest(original_clipboard).__dict__

        old_draft = ""
        try:
            await self.ui.hotkey("a", modifiers=["command down"])
            await self.ui.hotkey("x", modifiers=["command down"])
            await asyncio.sleep(0.05)
            old_draft = pbpaste()
        except Exception as e:
            debug_info.setdefault("warnings", []).append(f"剪切原草稿失败（忽略）：{e}")

        debug_info["oldDraftDigest"] = text_digest(old_draft).__dict__

        pbcopy(msg)
        await asyncio.sleep(0.05)
        await self.ui.hotkey("v", modifiers=["command down"])
        await asyncio.sleep(0.05)
        await self.ui.key_code(36)  # Enter 发送
        await asyncio.sleep(0.20)

        if old_draft.strip():
            pbcopy(old_draft)
            await asyncio.sleep(0.05)
            await self.ui.hotkey("v", modifiers=["command down"])
            await asyncio.sleep(0.05)

        pbcopy(original_clipboard)
        debug_info["clipboardDigestAfter"] = text_digest(original_clipboard).__dict__

        _dump_json_once()
        return {"message": f"已在企业微信给 {contact} 发送消息", "debug": debug_info if debug else None}
