from __future__ import annotations

import asyncio
import difflib
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend_py.services.macos_ui_automation import MacOSUIAutomation
from backend_py.services.ui_workflow_utils import (
    crop_png_with_sips,
    dump_json,
    get_ui_debug_dir,
    now_ms,
    pbcopy,
    roi_from_env,
    roi_norm_to_pixels,
)


DOUYIN_APP_NAMES: list[str] = ["抖音", "Douyin"]


@dataclass(frozen=True)
class DouyinVideoPick:
    text: str
    confidence: float
    similarity: float
    score: float
    x: float
    y: float
    width: float
    height: float


class DouyinController:
    """抖音 macOS App 的 OCR-first 自动化控制。

    设计原则：
    - 能用快捷键就优先使用快捷键（更稳更快）
    - 需要点击时，使用 window-targeted 截图 + OCR 框中心映射到屏幕坐标
    - 每一步都写入 ui_debug/<requestId>/，便于复盘排障
    """

    def __init__(self) -> None:
        self.ui = MacOSUIAutomation()

    @staticmethod
    def _norm_text(text: str) -> str:
        v = str(text or "")
        v = re.sub(r"\s+", "", v)
        v = v.replace("\uffff", "").replace("\ufffd", "")
        return v.strip().lower()

    @staticmethod
    def _split_query_tokens(query: str) -> list[str]:
        q = str(query or "").strip()
        if not q:
            return []

        parts = [p.strip() for p in re.split(r"\s+", q) if p.strip()]
        if len(parts) >= 2:
            return parts

        # 中文短句没有空格时：尽量按 2~4 字的片段做弱匹配。
        if len(q) <= 8:
            return [q]

        tokens: list[str] = []
        for n in (4, 3, 2):
            for i in range(0, max(0, len(q) - n + 1), n):
                t = q[i : i + n].strip()
                if len(t) >= 2:
                    tokens.append(t)
            if tokens:
                break
        return tokens or [q]

    def _score_candidates(
        self,
        content_boxes: Sequence[Any],
        *,
        q_norm: str,
        tokens: list[str],
    ) -> list["DouyinVideoPick"]:
        """对 OCR boxes 评分，返回候选视频列表。"""

        candidates: list[DouyinVideoPick] = []
        for b in content_boxes:
            text = str(getattr(b, "text", "") or "").strip()
            if not text:
                continue

            t_norm = self._norm_text(text)
            if not t_norm:
                continue

            # 过滤：过短文本通常是标签/按钮。
            if len(t_norm) < 4:
                continue

            conf = float(getattr(b, "confidence", 0.0) or 0.0)
            sim = difflib.SequenceMatcher(None, q_norm, t_norm).ratio() if q_norm else 0.0
            hit_count = sum(1 for t in tokens if t and self._norm_text(t) in t_norm)

            # 轻微偏向"更靠上"的结果：y 越小越好。
            y = float(getattr(b, "y", 0.0) or 0.0)
            score = float(sim) + float(hit_count) * 0.18 - float(y) * 0.0008

            candidates.append(
                DouyinVideoPick(
                    text=text,
                    confidence=conf,
                    similarity=float(sim),
                    score=float(score),
                    x=float(getattr(b, "x", 0.0) or 0.0),
                    y=y,
                    width=float(getattr(b, "width", 0.0) or 0.0),
                    height=float(getattr(b, "height", 0.0) or 0.0),
                )
            )
        return candidates

    async def _open_and_frontmost(self, *, debug: Dict[str, Any]) -> str:
        """打开并置前抖音，返回最终前台进程名（尽力）。"""

        opened = None
        for name in DOUYIN_APP_NAMES:
            subprocess.run(["open", "-a", name], capture_output=True, text=True, check=False)
            opened = name
            await asyncio.sleep(0.08)

        for _ in range(3):
            try:
                await self.ui.activate_app(DOUYIN_APP_NAMES[0])
            except Exception:
                pass
            try:
                await self.ui.set_process_frontmost(DOUYIN_APP_NAMES[0])
            except Exception:
                pass
            await asyncio.sleep(0.12)

            try:
                frontmost = await self.ui.get_frontmost_process_name()
            except Exception:
                frontmost = ""

            if str(frontmost).strip() in set(DOUYIN_APP_NAMES):
                debug.setdefault("frontmost", []).append({"ok": True, "process": frontmost})
                return str(frontmost).strip()

        debug.setdefault("frontmost", []).append({"ok": False, "opened": opened})
        return str(opened or DOUYIN_APP_NAMES[0])

    async def search_and_play(self, *, query: str, debug: bool = False, dry_run: bool = False) -> Dict[str, Any]:
        """搜索并播放与 query 最相关的视频（OCR-first）。

        Args:
            query: LLM 生成的搜索词
            debug: 返回 debug 信息
            dry_run: 只做截图/OCR/计算，不执行点击和键入
        """

        await self.ui.ensure_accessibility_ready()

        debug_info: Dict[str, Any] = {
            "mode": "douyin_ocr_workflow_v2",
            "query": str(query or "").strip(),
            "dryRun": bool(dry_run),
        }

        if not str(query or "").strip():
            raise RuntimeError("query 不能为空")

        # 统一窗口尺寸（按你的要求与 KuGou 对齐）。
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
            raise RuntimeError(f"抖音窗口归一化失败：{e}")

        await asyncio.sleep(0.20)

        rois = {
            # 左侧栏（精选/推荐/关注/我的…）
            "sidebar": roi_from_env("DOUYIN_SIDEBAR_ROI", default=(0.0, 0.12, 0.16, 0.84)),
            # 顶部搜索区域（包含输入框与右侧"搜索"按钮）
            "top_search": roi_from_env("DOUYIN_TOP_SEARCH_ROI", default=(0.16, 0.00, 0.84, 0.12)),
            # 搜索结果页顶部 tabs（综合/视频/用户/直播）——无 sidebar，tabs 紧贴左侧
            "results_tabs": roi_from_env("DOUYIN_RESULTS_TABS_ROI", default=(0.0, 0.06, 0.50, 0.07)),
            # 搜索结果页内容区——搜索结果页无 sidebar，内容从左边缘开始
            "results_content": roi_from_env("DOUYIN_RESULTS_CONTENT_ROI", default=(0.02, 0.22, 0.62, 0.76)),
        }
        debug_info["rois"] = rois

        # 异常路径兜底 JSON 落盘（与企微控制器一致）
        _json_dumped = False

        def _dump_json_once(tag: str = "douyin_workflow_debug") -> None:
            nonlocal _json_dumped
            if _json_dumped:
                return
            _json_dumped = True
            dump_json(tag, {"timestampMs": now_ms(), "debug": debug_info})

        try:
            return await self._search_and_play_inner(
                query=query,
                debug=debug,
                dry_run=dry_run,
                debug_info=debug_info,
                rois=rois,
                process_name=process_name,
                _dump_json_once=_dump_json_once,
            )
        except Exception:
            debug_info["error"] = True
            _dump_json_once("douyin_workflow_debug_error")
            raise

    async def _search_and_play_inner(
        self,
        *,
        query: str,
        debug: bool,
        dry_run: bool,
        debug_info: Dict[str, Any],
        rois: Dict[str, tuple],
        process_name: str,
        _dump_json_once: Any,
    ) -> Dict[str, Any]:
        """搜索并播放内部实现（拆分出来以便外层做异常兜底落盘）。"""

        cap0 = await self.ui.screenshot_window(owner_names=DOUYIN_APP_NAMES, tag="douyin_flow_init")
        debug_info.setdefault("captures", []).append({"step": "init", "capture": cap0})

        def _img_size(cap: Dict[str, Any]) -> tuple[float, float]:
            iw = float(((cap.get("imageSize") or {}).get("width")) or 0.0)
            ih = float(((cap.get("imageSize") or {}).get("height")) or 0.0)
            return (iw, ih)

        def _to_screen(cap: Dict[str, Any], *, image_x: float, image_y: float) -> tuple[float, float]:
            sx, sy = self.ui._to_screen_point_from_window_image_point(  # noqa: SLF001
                float(image_x),
                float(image_y),
                window_bounds=cap.get("windowBounds") or {},
                image_size=cap.get("imageSize") or {},
            )
            return (float(sx), float(sy))

        async def _ocr(cap: Dict[str, Any], *, roi: tuple[float, float, float, float], custom_words: Sequence[str]) -> list[Any]:
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

        async def _click_box_center(
            cap: Dict[str, Any],
            box: Any,
            *,
            step: str,
            clicks: int = 1,
            dx: float = 0.0,
            dy: float = 0.0,
        ) -> Dict[str, Any]:
            try:
                cx, cy = box.center()
            except Exception:
                cx = float(getattr(box, "x", 0.0) or 0.0) + float(getattr(box, "width", 0.0) or 0.0) / 2.0
                cy = float(getattr(box, "y", 0.0) or 0.0) + float(getattr(box, "height", 0.0) or 0.0) / 2.0

            image_x = float(cx) + float(dx)
            image_y = float(cy) + float(dy)
            sx, sy = _to_screen(cap, image_x=image_x, image_y=image_y)

            record = {
                "step": str(step),
                "imagePoint": {"x": float(image_x), "y": float(image_y)},
                "screenPoint": {"x": float(sx), "y": float(sy)},
                "clicks": int(clicks),
                "anchor": {
                    "text": str(getattr(box, "text", "") or ""),
                    "confidence": float(getattr(box, "confidence", 0.0) or 0.0),
                    "x": float(getattr(box, "x", 0.0) or 0.0),
                    "y": float(getattr(box, "y", 0.0) or 0.0),
                    "width": float(getattr(box, "width", 0.0) or 0.0),
                    "height": float(getattr(box, "height", 0.0) or 0.0),
                },
                "offset": {"dx": float(dx), "dy": float(dy)},
            }

            if not dry_run:
                await self.ui.click_at(float(sx), float(sy), clicks=int(clicks))

            debug_info.setdefault("clicks", []).append(record)
            return record

        def _has_any_text(boxes: Sequence[Any], keyword: str) -> bool:
            k = self._norm_text(keyword)
            if not k:
                return False
            for b in boxes:
                t = self._norm_text(str(getattr(b, "text", "") or ""))
                if k in t:
                    return True
            return False

        # 1) 判断是否已在搜索结果页（顶部 tabs: 综合/视频/用户/直播）。
        tabs_boxes = await _ocr(cap0, roi=rois["results_tabs"], custom_words=["综合", "视频", "用户", "直播"])  # type: ignore[arg-type]
        is_results_page = all(_has_any_text(tabs_boxes, t) for t in ["综合", "视频", "用户", "直播"])
        debug_info["state"] = {
            "isResultsPage": bool(is_results_page),
            "tabsPreview": [str(getattr(b, "text", "") or "") for b in tabs_boxes[:12]],
        }

        # 2) 如果不是结果页，确保能看到顶部搜索框。
        if not is_results_page:
            # 尝试点击左侧栏"精选"，使顶部搜索框出现（按你描述：精选页一定有）。
            sidebar_boxes = await _ocr(
                cap0,
                roi=rois["sidebar"],
                custom_words=["精选", "推荐", "关注", "朋友", "我的", "直播", "短剧"],
            )

            picked_jx = None
            for b in sidebar_boxes:
                if "精选" in self._norm_text(str(getattr(b, "text", "") or "")):
                    picked_jx = b
                    break

            if picked_jx is not None:
                await _click_box_center(cap0, picked_jx, step="sidebar_jingxuan", clicks=1)
                await asyncio.sleep(0.25)
            else:
                debug_info.setdefault("warnings", []).append("侧边栏未识别到'精选'，将继续尝试直接找顶部搜索框")

        # 3) 进入搜索输入态：优先通过"搜索按钮"向左偏移点击。
        cap1 = await self.ui.screenshot_window(owner_names=DOUYIN_APP_NAMES, tag="douyin_before_search_enter")
        debug_info.setdefault("captures", []).append({"step": "before_search_enter", "capture": cap1})

        search_boxes = await _ocr(
            cap1,
            roi=rois["top_search"],
            custom_words=["搜索", "搜索你感兴趣的内容"],
        )

        # 注意：top_search ROI 内可能识别到两类文本：
        # 1) 右侧按钮"搜索"（通常是短文本）；
        # 2) 输入框 placeholder"搜索你感兴趣的内容"（长文本）。
        # 两者点击策略不同：
        # - placeholder：直接点框中心或小偏移即可（避免越界点到桌面）
        # - 按钮"搜索"：点其左侧输入区，但必须做窗口边界夹取

        def _window_bounds(cap: Dict[str, Any]) -> Optional[Dict[str, float]]:
            wb = cap.get("windowBounds") if isinstance(cap.get("windowBounds"), dict) else None
            if not wb:
                return None
            try:
                return {
                    "x": float(wb.get("x") or 0.0),
                    "y": float(wb.get("y") or 0.0),
                    "width": float(wb.get("width") or 0.0),
                    "height": float(wb.get("height") or 0.0),
                }
            except Exception:
                return None

        def _clamp_screen_point(
            cap: Dict[str, Any],
            *,
            x: float,
            y: float,
            padding: float = 10.0,
        ) -> tuple[float, float, bool]:
            wb = _window_bounds(cap)
            if not wb or wb.get("width", 0.0) <= 1 or wb.get("height", 0.0) <= 1:
                return (float(x), float(y), False)

            min_x = float(wb["x"]) + float(padding)
            max_x = float(wb["x"]) + float(wb["width"]) - float(padding)
            min_y = float(wb["y"]) + float(padding)
            max_y = float(wb["y"]) + float(wb["height"]) - float(padding)

            cx = min(max(float(x), min_x), max_x)
            cy = min(max(float(y), min_y), max_y)
            changed = (abs(cx - float(x)) > 0.5) or (abs(cy - float(y)) > 0.5)
            return (float(cx), float(cy), bool(changed))

        def _find_best_search_anchor(boxes: Sequence[Any]) -> tuple[Optional[Any], str]:
            btn = None
            placeholder = None
            for b in boxes:
                t_raw = str(getattr(b, "text", "") or "")
                t = self._norm_text(t_raw)
                if not t:
                    continue
                if t == "搜索":
                    btn = b
                    break
                if "搜索你感兴趣的内容" in t or ("搜索" in t and len(t) >= 6):
                    placeholder = placeholder or b

            if btn is not None:
                return (btn, "button")
            if placeholder is not None:
                return (placeholder, "placeholder")
            return (None, "none")

        def _fallback_click_point(cap: Dict[str, Any]) -> Dict[str, Any]:
            iw, ih = _img_size(cap)
            if iw <= 1 or ih <= 1:
                raise RuntimeError("无法读取抖音截图尺寸")

            x_norm = float(rois["top_search"][0]) + float(rois["top_search"][2]) * 0.35
            y_norm = float(rois["top_search"][1]) + float(rois["top_search"][3]) * 0.25
            image_x = float(iw) * x_norm
            image_y = float(ih) * y_norm
            sx, sy = _to_screen(cap, image_x=image_x, image_y=image_y)
            sx, sy, changed = _clamp_screen_point(cap, x=sx, y=sy)
            return {
                "step": "enter_search_fallback_point",
                "imagePoint": {"x": float(image_x), "y": float(image_y)},
                "screenPoint": {"x": float(sx), "y": float(sy)},
                "clamped": bool(changed),
            }

        anchor, anchor_kind = _find_best_search_anchor(search_boxes)
        debug_info["searchAnchorKind"] = anchor_kind
        if anchor is None:
            record = _fallback_click_point(cap1)
            debug_info.setdefault("clicks", []).append(record)
            if not dry_run:
                await self.ui.click_at(float(record["screenPoint"]["x"]), float(record["screenPoint"]["y"]), clicks=1)
        else:
            if anchor_kind == "button":
                # 点按钮左侧输入区：偏移不要过大，并对最终屏幕点做窗口边界夹取。
                dx = -max(120.0, float(getattr(anchor, "width", 0.0) or 0.0) * 1.2)
                dy = 0.0
            else:
                # placeholder：直接点中心（轻微左偏移，避免误点右侧按钮区域）
                dx = -30.0
                dy = 0.0

            click_record = await _click_box_center(
                cap1,
                anchor,
                step=f"enter_search_{anchor_kind}",
                clicks=1,
                dx=dx,
                dy=dy,
            )

            sx = float((click_record.get("screenPoint") or {}).get("x") or 0.0)
            sy = float((click_record.get("screenPoint") or {}).get("y") or 0.0)
            sx2, sy2, changed = _clamp_screen_point(cap1, x=sx, y=sy)
            if changed:
                click_record["screenPointClamped"] = {"x": float(sx2), "y": float(sy2)}
                debug_info.setdefault("warnings", []).append("搜索入口点击点越界，已夹取到窗口内")
                if not dry_run:
                    await self.ui.click_at(float(sx2), float(sy2), clicks=1)

        # 点击后若抖音失焦，则立即置前（避免误点桌面导致后续键入进入其他应用/触发收起）。
        if not dry_run:
            try:
                frontmost = await self.ui.get_frontmost_process_name()
            except Exception:
                frontmost = ""
            if str(frontmost).strip() not in set(DOUYIN_APP_NAMES):
                debug_info.setdefault("warnings", []).append(
                    f"点击搜索入口后前台应用不是抖音（{frontmost}），将尝试重新置前"
                )
                try:
                    await self.ui.activate_app(DOUYIN_APP_NAMES[0])
                    await self.ui.set_process_frontmost(DOUYIN_APP_NAMES[0])
                except Exception as e:
                    debug_info.setdefault("warnings", []).append(f"重新置前抖音失败（忽略）：{e}")

        await asyncio.sleep(0.10)

        # 4) 清空并输入 query（粘贴更稳），然后回车搜索。
        if not dry_run:
            try:
                await self.ui.hotkey("a", modifiers=["command down"])
                await self.ui.key_code(51)
            except Exception as e:
                debug_info.setdefault("warnings", []).append(f"清空搜索框失败（忽略）：{e}")

            pbcopy(query)
            await asyncio.sleep(0.05)
            await self.ui.hotkey("v", modifiers=["command down"])
            await asyncio.sleep(0.05)
            await self.ui.key_code(36)

        # 5) 轮询等待搜索结果页 tabs 出现（取代固定 0.9 秒 sleep）。
        #    抖音搜索提交后，页面需要加载一段时间才会出现 tabs 和视频列表。
        poll_interval = 0.8
        poll_max_wait = 6.0
        poll_elapsed = 0.0
        cap2 = None
        video_tab = None

        await asyncio.sleep(0.6)  # 最小等待
        poll_elapsed += 0.6

        while poll_elapsed < poll_max_wait:
            cap2 = await self.ui.screenshot_window(owner_names=DOUYIN_APP_NAMES, tag="douyin_after_submit")
            debug_info.setdefault("captures", []).append({"step": f"after_submit_{poll_elapsed:.1f}s", "capture": cap2})

            tabs2 = await _ocr(
                cap2,
                roi=rois["results_tabs"],
                custom_words=["综合", "视频", "用户", "直播"],
            )

            # 精确匹配"视频"
            for b in tabs2:
                if self._norm_text(str(getattr(b, "text", "") or "")) == "视频":
                    video_tab = b
                    break

            # 宽松匹配
            if video_tab is None:
                for b in tabs2:
                    if "视频" in self._norm_text(str(getattr(b, "text", "") or "")):
                        video_tab = b
                        break

            if video_tab is not None:
                debug_info["tabsPollResult"] = {
                    "found": True,
                    "elapsedSec": round(poll_elapsed, 2),
                    "tabsPreview": [str(getattr(b, "text", "") or "") for b in tabs2[:12]],
                }
                break

            await asyncio.sleep(poll_interval)
            poll_elapsed += poll_interval

        if video_tab is None:
            debug_info["tabsPollResult"] = {
                "found": False,
                "elapsedSec": round(poll_elapsed, 2),
            }
            debug_info.setdefault("warnings", []).append(
                f"轮询 {poll_elapsed:.1f}s 未识别到'视频'tab（将继续在内容区找视频标题）"
            )
        else:
            # 点击"视频" tab
            await _click_box_center(cap2, video_tab, step="tab_video", clicks=1)

        # 6) 轮询等待视频内容加载完成（检测内容区有 ≥4 字的文本行）。
        content_poll_interval = 0.8
        content_poll_max = 5.0
        content_poll_elapsed = 0.0
        tokens = self._split_query_tokens(query)
        debug_info["queryTokens"] = tokens
        q_norm = self._norm_text(query)
        cap3 = None
        candidates_sorted: list[DouyinVideoPick] = []

        await asyncio.sleep(1.5)  # 点击 tab 后最小等待（视频封面/标题加载需要更长时间）
        content_poll_elapsed += 1.5

        while content_poll_elapsed < content_poll_max:
            cap3 = await self.ui.screenshot_window(owner_names=DOUYIN_APP_NAMES, tag="douyin_results_video")
            debug_info.setdefault("captures", []).append(
                {"step": f"results_video_{content_poll_elapsed:.1f}s", "capture": cap3}
            )

            content_boxes = await _ocr(
                cap3,
                roi=rois["results_content"],
                custom_words=list(dict.fromkeys([*tokens, query, "合集", "直播"])),
            )

            candidates = self._score_candidates(content_boxes, q_norm=q_norm, tokens=tokens)
            candidates_sorted = sorted(candidates, key=lambda c: (c.score, c.confidence), reverse=True)

            # 判断是否有有效视频标题（score > 0 且 similarity > 0.1 表示有实际内容）
            has_good_candidate = any(c.similarity > 0.1 for c in candidates_sorted[:5])
            # 至少有 3 个长文本候选（过滤掉"问问AI"等 UI 元素）
            has_enough_content = len(candidates_sorted) >= 3

            if (has_good_candidate or has_enough_content) and content_poll_elapsed >= 2.0:
                debug_info["contentPollResult"] = {
                    "found": True,
                    "elapsedSec": round(content_poll_elapsed, 2),
                    "candidateCount": len(candidates_sorted),
                    "topSimilarity": round(candidates_sorted[0].similarity, 3) if candidates_sorted else 0.0,
                }
                break

            await asyncio.sleep(content_poll_interval)
            content_poll_elapsed += content_poll_interval

        if not candidates_sorted:
            debug_info["contentPollResult"] = {
                "found": False,
                "elapsedSec": round(content_poll_elapsed, 2),
            }

        debug_info["pickPreview"] = [
            {
                "text": c.text,
                "confidence": c.confidence,
                "similarity": c.similarity,
                "score": c.score,
                "y": c.y,
            }
            for c in candidates_sorted[:12]
        ]

        if not candidates_sorted:
            # 落盘证据：OCR boxes + ROI crop。
            out_dir = get_ui_debug_dir()
            screenshot_path = str((cap3 or {}).get("screenshotPath") or "")
            iw, ih = _img_size(cap3 or {})
            roi_px = roi_norm_to_pixels(rois["results_content"], image_w=iw, image_h=ih)

            boxes_json_path = dump_json(
                "douyin_results_content_ocr",
                {
                    "timestampMs": now_ms(),
                    "query": query,
                    "roiNormalized": rois["results_content"],
                    "roiPixels": roi_px,
                    "screenshotPath": screenshot_path,
                    "previewTop12": [str(getattr(b, "text", "") or "") for b in (content_boxes if 'content_boxes' in dir() else [])[:12]],
                    "boxes": [
                        {
                            "text": str(getattr(b, "text", "") or ""),
                            "confidence": float(getattr(b, "confidence", 0.0) or 0.0),
                            "x": float(getattr(b, "x", 0.0) or 0.0),
                            "y": float(getattr(b, "y", 0.0) or 0.0),
                            "width": float(getattr(b, "width", 0.0) or 0.0),
                            "height": float(getattr(b, "height", 0.0) or 0.0),
                        }
                        for b in (content_boxes if 'content_boxes' in dir() else [])[:160]
                    ],
                },
            )

            crop_path = out_dir / f"douyin_results_content_roi_{now_ms()}.png"
            crop_ok = bool(screenshot_path) and crop_png_with_sips(
                screenshot_path=screenshot_path,
                roi_pixels=roi_px,
                out_path=crop_path,
            )

            raise RuntimeError(
                "未能在抖音结果内容区找到可匹配的标题文本。"
                f"已导出 OCR 证据：{boxes_json_path}；roiCropOk={crop_ok}"
            )

        best = candidates_sorted[0]
        # 点击标题框中心（轻微向下偏移，尽量落在正文区域而非上方标签）。
        click_dx = 0.0
        click_dy = max(6.0, best.height * 0.10)

        if dry_run:
            debug_info["picked"] = best.__dict__
            debug_info["pickedClickOffset"] = {"dx": float(click_dx), "dy": float(click_dy)}
            _dump_json_once()
            return {"message": f"(dry-run) 将在抖音搜索并播放：{query}", "debug": debug_info if debug else None}

        # 先把 best 还原为一个"伪 box"，复用 click helper。
        class _Box:
            def __init__(self, pick: DouyinVideoPick) -> None:
                self.text = pick.text
                self.confidence = pick.confidence
                self.x = pick.x
                self.y = pick.y
                self.width = pick.width
                self.height = pick.height

            def center(self) -> tuple[float, float]:
                return (self.x + self.width / 2.0, self.y + self.height / 2.0)

        await _click_box_center(cap3, _Box(best), step="play_best_match_video", clicks=1, dx=click_dx, dy=click_dy)

        # 触发播放后可选：用快捷键"空格"暂停/继续可验证，但默认不做，避免副作用。
        await asyncio.sleep(0.25)

        debug_info["picked"] = best.__dict__
        debug_info["pickedClickOffset"] = {"dx": float(click_dx), "dy": float(click_dy)}
        _dump_json_once()

        return {"message": f"已在抖音搜索并尝试播放：{query}", "debug": debug_info if debug else None}
