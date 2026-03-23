from __future__ import annotations

import asyncio
import difflib
import os
import re
import subprocess
import uuid
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

    def _is_noise_text(self, text: str) -> bool:
        """判断 OCR 文本是否明显不可能是联系人/会话标题。

        目的：避免 ROI 偏大或 OCR 抽到聊天正文/草稿内容时，把长句当成联系人候选，
        导致“直达会话/进入会话校验”误判并触发误发护栏中止。
        """

        raw = str(text or "").strip()
        t = self._norm_text(raw)
        if not t:
            return True

        # 常见无意义时间戳（会话列表右侧）
        if re.fullmatch(r"\d{1,2}:\d{2}", t):
            return True

        # 正文样式：编号条目/段落符号（例如 "1. xxx" / "2) xxx" / "3、xxx"）
        if re.match(r"^\s*\d+\s*[.)、]", raw):
            return True

        # 正文样式：较长且包含明显标点（更像句子而非人名/群名）
        if len(raw) >= 12 and any(p in raw for p in ["。", "，", ",", "：", ":", "；", ";", "？", "!", "！", "/", "|"]):
            return True

        # 结果列表中的学号/编号等（纯数字且较长）通常不是联系人名
        if re.fullmatch(r"\d{6,}", raw.strip()):
            return True

        # “没有找到相关结果 / 智能搜索提示”属于空态文案，不应被当作可点击结果
        if "没有找到相关结果" in raw:
            return True
        if "智能搜索" in raw:
            return True

        # 会话列表常见标签噪声（草稿/文件/图片等）
        if "草稿" in t or "图片" in t or "文件" in t:
            return True

        return False

    def _candidate_text_variants_for_match(self, text: str) -> list[str]:
        """生成用于匹配的候选文本变体（均为 _norm_text 后的形式）。

        目的：联系人/群聊在 UI 中常以“姓名 + 后缀信息”的形式出现，例如：
        - 罗晨曦@深圳大学
        - 顾老师（导师）
        直接对整串做 SequenceMatcher 会被“后缀长度惩罚”压低分数，导致误判。
        """

        raw = str(text or "").strip()
        norm_full = self._norm_text(raw)
        if not norm_full:
            return []

        variants: list[str] = [norm_full]

        # 常见分隔符：@ / 括号 / 竖线
        for delim in ["@", "（", "(", "【", "[", "|"]:
            idx = norm_full.find(delim)
            if idx > 0:
                variants.append(norm_full[:idx])

        # 去重，保持顺序
        seen: set[str] = set()
        out: list[str] = []
        for v in variants:
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def _best_match_box(self, boxes: Sequence[Any], *, target: str, min_conf: float) -> Optional[WeComPick]:
        """在 OCR boxes 中选择最可能匹配 target 的候选。"""

        t_norm = self._norm_text(target)
        if not t_norm:
            return None

        best: Optional[WeComPick] = None
        for b in boxes:
            conf = float(getattr(b, "confidence", 0.0) or 0.0)
            if conf < float(min_conf):
                continue

            txt = str(getattr(b, "text", "") or "").strip()
            if self._is_noise_text(txt):
                continue

            variants = self._candidate_text_variants_for_match(txt)
            if not variants:
                continue

            # 方案 A：若目标是候选的子串（如 “罗晨曦” in “罗晨曦@深圳大学”），视为强命中。
            # 仅对长度>=2的目标启用，避免单字（如“师”）造成过宽匹配。
            if len(t_norm) >= 2 and any(t_norm in v for v in variants):
                sim = 1.0
            else:
                sim = max(difflib.SequenceMatcher(None, t_norm, v).ratio() for v in variants)

            # 轻微长度加成：当相似度接近时，更偏向“完整会话名”而非极短片段。
            # 注意：这里用“全量 norm 文本”的长度做加成，避免因截断变体过短导致不公平。
            score = float(sim) + min(0.10, len(variants[0]) * 0.0015)
            cand = WeComPick(text=txt, confidence=conf, similarity=float(sim), score=float(score))
            if best is None or (cand.score, cand.confidence) > (best.score, best.confidence):
                best = cand

        return best

    async def _cut_chat_input_draft_with_sentinel(self, *, debug_info: Dict[str, Any]) -> tuple[str, bool]:
        """从当前焦点输入框中剪切草稿到剪贴板，并返回草稿内容。

        设计目标：
        - 仅当 **确实剪切到了输入框文本** 时，才允许后续“草稿回填”。
        - 避免“输入框为空 / 剪切失败”时，把剪贴板里原本内容（例如联系人名）误当成草稿回填，
          造成发送后又多粘贴一遍联系人名的现象。

        判定策略（哨兵 sentinel）：
        - 先把剪贴板设置为唯一哨兵；
        - 执行 Cmd+A / Cmd+X；
        - 若剪贴板仍为哨兵，视为未剪切到草稿（cut_ok=False）。
        """

        sentinel = f"__VA_DRAFT_SENTINEL__{uuid.uuid4().hex[:12]}"
        debug_info["draftCutSentinelDigest"] = text_digest(sentinel).__dict__

        try:
            pbcopy(sentinel)
            await asyncio.sleep(0.02)
            await self.ui.hotkey("a", modifiers=["command down"])
            await self.ui.hotkey("x", modifiers=["command down"])
            await asyncio.sleep(0.05)
            got = pbpaste()
        except Exception as e:
            debug_info.setdefault("warnings", []).append(f"剪切原草稿失败（忽略）：{e}")
            debug_info["draftCutOk"] = False
            return ("", False)

        if got == sentinel:
            debug_info["draftCutOk"] = False
            return ("", False)

        debug_info["draftCutOk"] = True
        return (str(got or ""), True)

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
            # 注意：ROI 语义为 (x, y, w, h)。旧默认值 w/h 偏大，会吃进聊天正文/输入区，
            # 导致把长句正文当成联系人候选（见 ui_debug directChatPick 误命中证据）。
            "chat_list": roi_from_env("WECOM_CHAT_LIST_ROI", default=(0.14, 0.16, 0.16, 0.62)),
            # 聊天窗口顶部标题区（发送前校验，防误发）
            # 实测标题文字位于窗口上方偏左；为了同时覆盖联系人名与其右侧的辅助信息，
            # x 起点需略向左扩展，避免 OCR 只读到右侧文本而错过联系人名。
            "chat_header": roi_from_env("WECOM_CHAT_HEADER_ROI", default=(0.24, 0.00, 0.46, 0.09)),
            # Shift+Cmd+F 全局搜索弹窗中的搜索区域
            "search_popup": roi_from_env("WECOM_SEARCH_POPUP_ROI", default=(0.02, 0.02, 0.55, 0.20)),
            # 全局搜索弹窗顶部 tabs（联系人/群聊/聊天记录…）
            "global_tabs": roi_from_env("WECOM_GLOBAL_TABS_ROI", default=(0.12, 0.10, 0.70, 0.12)),
            # 全局搜索结果区
            "results_list": roi_from_env("WECOM_RESULTS_LIST_ROI", default=(0.12, 0.22, 0.86, 0.70)),
        }
        popup_rois = {
            # 全局搜索弹窗 tabs 行（必须足够窄，避免把“搜索结果里出现的‘联系人’”识别进来造成干扰）
            "tabs": roi_from_env("WECOM_POPUP_TABS_ROI", default=(0.02, 0.14, 0.96, 0.085)),
            # 全局搜索弹窗结果列表区（切到“联系人”tab 后，用于定位联系人条目）
            # 关键：y 起点必须足够靠上覆盖“第一条联系人结果卡片”，否则会出现
            # “肉眼可见顾老师，但 OCR 只读到空态文案”的误判（见 requestId=827e8e43... 证据）。
            "results": roi_from_env("WECOM_POPUP_RESULTS_ROI", default=(0.02, 0.225, 0.96, 0.70)),
        }
        debug_info["rois"] = rois
        debug_info["popupRois"] = popup_rois

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

        def _has_any(boxes: Sequence[Any], keyword: str) -> bool:
            k = self._norm_text(keyword)
            if not k:
                return False
            for b in boxes:
                t = self._norm_text(str(getattr(b, "text", "") or ""))
                if k in t:
                    return True
            return False

        def _pick_tab_box(boxes: Sequence[Any], *, label: str) -> Optional[Any]:
            """在 tabs ROI 的 OCR 结果中选择指定 tab（如“联系人”）。"""
            want = self._norm_text(label)
            if not want:
                return None

            best_box: Optional[Any] = None
            best_score = -1.0
            for b in boxes:
                txt = str(getattr(b, "text", "") or "").strip()
                t = self._norm_text(txt)
                if not t:
                    continue
                # tabs 行通常是短词：允许包含/等于，但不在此处做“结果区”的误命中（ROI 已严格限制）。
                if want not in t:
                    continue
                conf = float(getattr(b, "confidence", 0.0) or 0.0)
                # 偏好更“干净”的匹配：完全等于 > 包含
                exact_bonus = 0.6 if t == want else 0.2
                score = float(conf) + float(exact_bonus)
                if score > best_score:
                    best_score = score
                    best_box = b

            return best_box

        def _pick_first_result_box(boxes: Sequence[Any]) -> Optional[Any]:
            """选择结果列表的“第一条”候选，用于兜底点击（方案 B）。

            说明：OCR 返回的是多个文本框而不是结构化行；这里用 y 坐标最小的“非噪声文本框”
            近似代表第一条结果的可点击区域。点击后仍会通过 chat_header 护栏验证，避免误发。
            """

            best_box: Optional[Any] = None
            best_key: Optional[tuple[float, float, float]] = None  # (y, x, -conf)
            for b in boxes:
                txt = str(getattr(b, "text", "") or "").strip()
                if self._is_noise_text(txt):
                    continue
                try:
                    y = float(getattr(b, "y", 1e9) or 1e9)
                    x = float(getattr(b, "x", 1e9) or 1e9)
                except Exception:
                    continue
                conf = float(getattr(b, "confidence", 0.0) or 0.0)
                key = (y, x, -conf)
                if best_key is None or key < best_key:
                    best_key = key
                    best_box = b
            return best_box

        async def _verify_chat_header_or_raise() -> None:
            cap_hdr = await self.ui.screenshot_window(owner_names=WECOM_APP_NAMES, tag="wecom_chat_header_verify")
            debug_info.setdefault("captures", []).append({"step": "chat_header_verify", "capture": cap_hdr})

            hdr_boxes = await _ocr(cap_hdr, roi=rois["chat_header"], custom_words=[contact])
            debug_info["chatHeaderPreview"] = [str(getattr(b, "text", "") or "") for b in hdr_boxes[:10]]

            if _has_any(hdr_boxes, contact):
                return

            pick = self._best_match_box(hdr_boxes, target=contact, min_conf=0.30)
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
                        retry_pick = self._best_match_box(retry_boxes, target=contact, min_conf=0.35)
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
        direct_pick = self._best_match_box(list_boxes, target=contact, min_conf=0.35)
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
            # 说明：
            # - 旧实现采用“纯键盘 Return 选第一条综合结果”，当第一条是群聊时会进入错误会话并触发护栏中止。
            # - 新实现：截取全局搜索弹窗窗口 -> 在严格 ROI 内点击“联系人”tab -> 在结果列表中点联系人。
            # - 若弹窗窗口无法截取（Quartz 枚举不到），则回退到旧键盘策略（仍会做标题校验护栏，避免误发）。
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
                await asyncio.sleep(0.45)  # 先短等，后续会用“弹窗结果是否出现”做重试

                debug_info["globalSearchKeyboard"][-1]["step"] = "input_done"

                # D. 尝试截取全局搜索弹窗窗口，并点击“联系人”tab
                cap_parent = cap1  # 主窗口 bounds 用于筛选“位于主窗口内部”的弹窗窗口
                popup_opened = False
                clicked_contacts = False
                clicked_result = False
                popup_attempts: list[dict[str, Any]] = []
                contacts_tab_selected = False

                for popup_try in range(2):
                    try:
                        cap_popup = await self.ui.screenshot_window_child_in_parent(
                            owner_names=WECOM_APP_NAMES,
                            parent_window_bounds=cap_parent.get("windowBounds") or {},
                            tag=f"wecom_global_search_popup_{attempt}_{popup_try}",
                        )
                        debug_info.setdefault("captures", []).append(
                            {"step": f"global_search_popup_{attempt}_{popup_try}", "capture": cap_popup}
                        )
                        popup_opened = True

                        popup_attempts.append({"try": int(popup_try)})

                        # B：重试时不要重复点击“联系人”tab（企微可能会刷新/清空当前结果）。
                        # 仅在本 attempt 第一次进入弹窗时尝试切到联系人。
                        if not contacts_tab_selected:
                            tab_boxes = await _ocr(
                                cap_popup,
                                roi=popup_rois["tabs"],
                                custom_words=["联系人", "全部", "群聊", "聊天记录"],
                            )
                            popup_attempts[-1]["tabsPreview"] = [
                                str(getattr(b, "text", "") or "") for b in tab_boxes[:12]
                            ]

                            tab_box = _pick_tab_box(tab_boxes, label="联系人")
                            popup_attempts[-1]["tabPick"] = (
                                str(getattr(tab_box, "text", "") or "") if tab_box else None
                            )
                            if tab_box is not None:
                                await _click_box_center(
                                    cap_popup,
                                    tab_box,
                                    step=f"popup_click_contacts_tab_{attempt}_{popup_try}",
                                )
                                clicked_contacts = True
                                contacts_tab_selected = True
                                await asyncio.sleep(0.35)
                            else:
                                # 如果 tabs OCR 都找不到联系人，则本轮不继续冒险点结果。
                                await asyncio.sleep(0.20)
                                continue

                        # C：结果可能需要一点时间稳定渲染。这里做有限轮询（不会无限等待）。
                        poll_delays = [0.20, 0.35, 0.55, 0.80]
                        cap_popup2 = None
                        result_boxes: list[Any] = []
                        for pi, delay in enumerate(poll_delays):
                            cap_popup2 = await self.ui.screenshot_window_child_in_parent(
                                owner_names=WECOM_APP_NAMES,
                                parent_window_bounds=cap_parent.get("windowBounds") or {},
                                tag=f"wecom_global_search_popup_results_{attempt}_{popup_try}_{pi}",
                            )
                            debug_info.setdefault("captures", []).append(
                                {
                                    "step": f"global_search_popup_results_{attempt}_{popup_try}_{pi}",
                                    "capture": cap_popup2,
                                }
                            )

                            result_boxes = await _ocr(
                                cap_popup2,
                                roi=popup_rois["results"],
                                custom_words=[contact],
                            )
                            preview = [str(getattr(b, "text", "") or "") for b in result_boxes[:14]]
                            popup_attempts[-1][f"resultsPreview_{pi}"] = preview

                            # 如果只读到空态提示，则继续等待（不视为有效结果）
                            meaningful = False
                            for s in preview:
                                if not self._is_noise_text(s):
                                    meaningful = True
                                    break
                            if meaningful:
                                break

                            await asyncio.sleep(delay)

                        popup_attempts[-1]["resultsPreview"] = [
                            str(getattr(b, "text", "") or "") for b in result_boxes[:14]
                        ]

                        pick = self._best_match_box(result_boxes, target=contact, min_conf=0.35)
                        popup_attempts[-1]["resultPick"] = pick.__dict__ if pick else None
                        if pick is None or pick.similarity < 0.78:
                            # 方案 B（兜底）：如果结果区确实有 OCR 文本，但匹配阈值不达标，
                            # 尝试点击“第一条结果”，随后仍会用 chat_header 护栏确认是否进入目标会话。
                            fallback_box = _pick_first_result_box(result_boxes) if result_boxes else None
                            popup_attempts[-1]["fallbackFirstBox"] = (
                                str(getattr(fallback_box, "text", "") or "") if fallback_box else None
                            )
                            if fallback_box is not None and popup_try >= 1:
                                await _click_box_center(
                                    cap_popup2 or cap_popup,
                                    fallback_box,
                                    step=f"popup_click_first_result_fallback_{attempt}_{popup_try}",
                                )
                                clicked_result = True
                                await asyncio.sleep(0.55)
                                break

                            # 结果可能未加载出来：按你要求“清空并重试搜索”
                            try:
                                await self.ui.hotkey("a", modifiers=["command down"])
                                await self.ui.key_code(51)  # Delete
                                await asyncio.sleep(0.05)
                                pbcopy(contact)
                                await asyncio.sleep(0.03)
                                await self.ui.hotkey("v", modifiers=["command down"])
                                await asyncio.sleep(0.25)
                            except Exception as e:
                                debug_info.setdefault("warnings", []).append(f"弹窗内重试搜索失败（忽略）：{e}")
                            continue

                        # 点击匹配到的联系人条目
                        for b in result_boxes:
                            txt = str(getattr(b, "text", "") or "").strip()
                            if self._norm_text(txt) == self._norm_text(pick.text):
                                await _click_box_center(
                                    cap_popup2,
                                    b,
                                    step=f"popup_click_contact_result_{attempt}_{popup_try}",
                                )
                                clicked_result = True
                                await asyncio.sleep(0.55)  # 等待会话切换
                                break

                        if clicked_result:
                            break

                    except Exception as e:
                        popup_attempts.append({"try": int(popup_try), "error": str(e)})
                        await asyncio.sleep(0.25)

                debug_info.setdefault("globalSearchPopup", []).append(
                    {
                        "attempt": int(attempt),
                        "popupOpened": bool(popup_opened),
                        "clickedContactsTab": bool(clicked_contacts),
                        "clickedResult": bool(clicked_result),
                        "tries": popup_attempts,
                    }
                )

                if clicked_result:
                    # E. 用 Escape 关闭可能残留的弹窗（确保焦点回到聊天区域）
                    await self.ui.key_code(53)  # Escape
                    await asyncio.sleep(0.25)
                    debug_info["globalSearchKeyboard"][-1]["step"] = "popup_closed"
                else:
                    # Fallback：若弹窗截取失败（popupOpened=False），才回退到旧键盘 Return 选第一条；
                    # 若能截到弹窗但无法点到“联系人/结果”，继续下一轮 attempt（避免误点综合结果）。
                    if not popup_opened:
                        await self.ui.key_code(36)  # Return
                        await asyncio.sleep(0.5)
                        debug_info["globalSearchKeyboard"][-1]["step"] = "return_pressed_fallback"
                        await self.ui.key_code(53)
                        await asyncio.sleep(0.25)
                        debug_info["globalSearchKeyboard"][-1]["step"] = "popup_closed"
                    else:
                        debug_info["globalSearchKeyboard"][-1]["step"] = "popup_click_failed"
                        await asyncio.sleep(0.25)
                        # 进入下一次 attempt 重试
                        pass

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

                hdr_pick = self._best_match_box(hdr_boxes, target=contact, min_conf=0.30)
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

        old_draft, cut_ok = await self._cut_chat_input_draft_with_sentinel(debug_info=debug_info)

        debug_info["oldDraftDigest"] = text_digest(old_draft).__dict__

        pbcopy(msg)
        await asyncio.sleep(0.05)
        await self.ui.hotkey("v", modifiers=["command down"])
        await asyncio.sleep(0.05)
        await self.ui.key_code(36)  # Enter 发送
        await asyncio.sleep(0.20)

        # 仅当确实从输入框剪切到了草稿时，才允许回填；避免误把剪贴板内容回填进输入框。
        if cut_ok and old_draft.strip():
            pbcopy(old_draft)
            await asyncio.sleep(0.05)
            await self.ui.hotkey("v", modifiers=["command down"])
            await asyncio.sleep(0.05)

        pbcopy(original_clipboard)
        debug_info["clipboardDigestAfter"] = text_digest(original_clipboard).__dict__

        _dump_json_once()
        return {"message": f"已在企业微信给 {contact} 发送消息", "debug": debug_info if debug else None}
