from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from backend_py.config_manager import UI_AUTOMATION_CONFIG
from backend_py.services.macos_ui_automation import MacOSUIAutomation
from backend_py.services.ollama_client import OllamaClient


KUGOU_APP_NAMES = ["酷狗音乐", "KugouMusic", "Kugou Music"]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _debug_dir() -> Path:
    """按 runId 返回 ui_debug 目录。

    说明：
    - 目录约定：~/Documents/VoiceAssistant/ui_debug/<runId>/
    - runId 来自 VOICE_ASSISTANT_DEBUG_RUN（ToolRouter 会按 requestId 自动设置）。
    """

    base = Path.home() / "Documents" / "VoiceAssistant" / "ui_debug"
    run_id = str(os.environ.get("VOICE_ASSISTANT_DEBUG_RUN") or "").strip()
    if run_id:
        safe = re.sub(r"[^0-9a-zA-Z_.-]+", "_", run_id).strip("_")
        safe = safe[:120] if safe else ""
        if safe:
            base = base / safe

    base.mkdir(parents=True, exist_ok=True)
    return base


def _read_base64(path: str) -> str:
    raw = Path(str(path)).read_bytes()
    return base64.b64encode(raw).decode("utf-8")


def _extract_first_json_object(text: str, *, anchor: str) -> str:
    """从模型输出中提取第一个 JSON object（支持前缀说明文本）。"""

    raw = str(text or "")
    idx = raw.find(anchor)
    if idx >= 0:
        raw = raw[idx + len(anchor) :]

    start = raw.find("{")
    if start < 0:
        raise ValueError("未找到 JSON 起始 '{'")

    s = raw[start:]
    depth = 0
    in_str = False
    escape = False

    for i, ch in enumerate(s):
        if escape:
            escape = False
            continue

        if ch == "\\":
            if in_str:
                escape = True
            continue

        if ch == '"':
            in_str = not in_str
            continue

        if in_str:
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[: i + 1]

    raise ValueError("JSON 括号不匹配，无法提取完整对象")


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _parse_bbox_norm(obj: Any) -> Optional[Dict[str, float]]:
    if not isinstance(obj, dict):
        return None

    try:
        x = _clamp01(float(obj.get("x")))
        y = _clamp01(float(obj.get("y")))
        w = _clamp01(float(obj.get("w")))
        h = _clamp01(float(obj.get("h")))
    except Exception:
        return None

    w = max(0.0, min(1.0 - x, w))
    h = max(0.0, min(1.0 - y, h))
    if w <= 0.0 or h <= 0.0:
        return None

    return {"x": x, "y": y, "w": w, "h": h}


def _bbox_area(bbox_norm: Mapping[str, Any]) -> float:
    try:
        w = float(bbox_norm.get("w") or 0.0)
        h = float(bbox_norm.get("h") or 0.0)
        return float(w) * float(h)
    except Exception:
        return 0.0


def _sips_resize_png_max_side(*, src_png: str, out_dir: Path, tag: str, max_side: int) -> str:
    """用 `sips` 将 PNG 按最长边缩放到指定像素。

    说明：
    - 只影响“送入模型”的输入图；原始窗口截图不变。
    - `max_side<=0` 时表示不缩放，直接返回原图路径。

    Returns:
        resized_png_path
    """

    src = str(src_png or "").strip()
    if not src:
        raise RuntimeError("src_png 为空")

    max_side_int = int(max_side)
    if max_side_int <= 0:
        return src

    max_side_int = int(max(64, min(2048, max_side_int)))
    out_path = out_dir / f"{str(tag).strip() or 'vlm'}_scaled_{max_side_int}_{_now_ms()}.png"

    proc = subprocess.run(
        ["sips", "-Z", str(max_side_int), src, "--out", str(out_path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        stderr = str(proc.stderr or "").strip()
        raise RuntimeError(f"sips 缩放失败：{stderr[:400]}")

    if not out_path.exists() or out_path.stat().st_size <= 0:
        raise RuntimeError("sips 缩放失败：输出文件不存在或为空")

    return str(out_path)


def _ensure_webp_q95_from_png(*, src_png: str, out_dir: Path, tag: str) -> str:
    """将 PNG 转为 WebP（quality=95）。

    约束（用户确认）：
    - VLM 输入图片必须是 WebP。
    - 不提供 PNG/JPEG 自动兜底。
    - 若 WebP 转换失败：fail-fast + 由上层落盘错误证据。

    实现策略：
    - 优先使用 `cwebp`（若存在）。
    - 否则尝试 `sips`（macOS 内置）。

    Returns:
        webp_path
    """

    src = str(src_png or "").strip()
    if not src:
        raise RuntimeError("src_png 为空")

    out_path = out_dir / f"{str(tag).strip() or 'vlm'}_img_{_now_ms()}.webp"

    cwebp = shutil.which("cwebp")
    if cwebp:
        proc = subprocess.run(
            [
                cwebp,
                "-q",
                "95",
                src,
                "-o",
                str(out_path),
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
            return str(out_path)

        stderr = str(proc.stderr or "").strip()
        raise RuntimeError(f"cwebp 转换失败：{stderr[:400]}")

    # 兜底：sips（不同 macOS 版本对 webp 支持不一，失败时直接报错）
    proc = subprocess.run(
        [
            "sips",
            "-s",
            "format",
            "webp",
            "-s",
            "formatOptions",
            "95",
            src,
            "--out",
            str(out_path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
        return str(out_path)

    stderr = str(proc.stderr or "").strip()
    raise RuntimeError(
        "WebP 转换失败（缺少 cwebp，且 sips 不支持或执行失败）。"
        "请安装 libwebp 提供 cwebp（或升级系统以支持 sips webp）。"
        f" stderr={stderr[:300]}"
    )


def _load_playbook_json() -> Dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "backend_py" / "resources" / "kugou_vlm_playbook_v1.json"
    if not path.exists():
        raise RuntimeError(f"缺少 VLM playbook 文件：{path}")

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"读取 VLM playbook 失败：{e}")


def _compact_playbook_for_prompt(pb: Mapping[str, Any]) -> Dict[str, Any]:
    allow = pb.get("state_target_allow") if isinstance(pb.get("state_target_allow"), dict) else {}
    allow_compact: Dict[str, List[str]] = {}
    for k, v in (allow or {}).items():
        if not isinstance(k, str):
            continue
        if isinstance(v, list):
            allow_compact[k] = [str(x) for x in v if str(x)]

    return {
        "version": str(pb.get("version") or ""),
        "states": [str(x) for x in (pb.get("states") or []) if str(x)],
        "targets": [str(x) for x in (pb.get("targets") or []) if str(x)],
        "state_target_allow": allow_compact,
        "notes": pb.get("notes") if isinstance(pb.get("notes"), dict) else {},
    }


@dataclass(frozen=True)
class VlmPlan:
    state: str
    state_confidence: float
    action: str
    target: str
    bbox_norm: Optional[Dict[str, float]]
    text: str
    submit: bool
    done: bool
    idempotent_ok: bool
    action_confidence: float
    evidence: Tuple[str, ...]


def _parse_plan(content: str) -> Tuple[VlmPlan, Dict[str, Any]]:
    json_str = _extract_first_json_object(content, anchor="UI_PLAN_JSON")
    data = json.loads(json_str)
    if not isinstance(data, dict):
        raise ValueError("UI_PLAN_JSON 必须是 object")

    state = str(data.get("state") or "").strip().lower()
    action = str(data.get("action") or "").strip().lower()
    target = str(data.get("target") or "").strip().lower()

    bbox_norm = _parse_bbox_norm(data.get("bbox_norm"))

    text = str(data.get("text") or "")
    submit = bool(data.get("submit") is True)
    done = bool(data.get("done") is True)
    idempotent_ok = bool(data.get("idempotent_ok") is True)

    try:
        state_conf = float(data.get("state_confidence") or 0.0)
    except Exception:
        state_conf = 0.0

    try:
        action_conf = float(data.get("action_confidence") or 0.0)
    except Exception:
        action_conf = 0.0

    ev = data.get("evidence")
    evidence: List[str] = []
    if isinstance(ev, list):
        for x in ev[:3]:
            s = str(x or "").strip()
            if s:
                evidence.append(s)

    return (
        VlmPlan(
            state=state,
            state_confidence=float(max(0.0, min(1.0, state_conf))),
            action=action,
            target=target,
            bbox_norm=bbox_norm,
            text=text,
            submit=submit,
            done=done,
            idempotent_ok=idempotent_ok,
            action_confidence=float(max(0.0, min(1.0, action_conf))),
            evidence=tuple(evidence),
        ),
        data,
    )


def _validate_plan(
    plan: VlmPlan,
    *,
    allow_actions: Sequence[str],
    playbook: Mapping[str, Any],
    bbox_area_min: float,
) -> Tuple[bool, List[str]]:
    errors: List[str] = []

    if plan.action not in set(allow_actions):
        errors.append(f"action 不在 allowlist：{plan.action}")

    pb_states = set(str(x) for x in (playbook.get("states") or []) if str(x))
    pb_targets = set(str(x) for x in (playbook.get("targets") or []) if str(x))

    if plan.state not in pb_states:
        errors.append(f"state 非法或不在 playbook：{plan.state}")

    if plan.target and plan.target not in pb_targets:
        errors.append(f"target 非法或不在 playbook：{plan.target}")

    if plan.done:
        if plan.action != "noop":
            errors.append("done=true 时 action 必须为 noop")

    if plan.action in {"click", "type_text"}:
        if plan.bbox_norm is None:
            errors.append(f"action={plan.action} 时必须提供 bbox_norm")
        else:
            area = _bbox_area(plan.bbox_norm)
            if area < float(bbox_area_min):
                errors.append(f"bbox 面积过小：{area:.6f} < {float(bbox_area_min):.6f}")

    if plan.action == "type_text":
        if not plan.text.strip():
            errors.append("type_text 时 text 不能为空")

    # state -> target 约束
    allow_map = playbook.get("state_target_allow")
    allow_by_state: Dict[str, List[str]] = {}
    if isinstance(allow_map, dict):
        for k, v in allow_map.items():
            if not isinstance(k, str) or not isinstance(v, list):
                continue
            allow_by_state[str(k)] = [str(x) for x in v if str(x)]

    allowed_targets = allow_by_state.get(plan.state)
    if isinstance(allowed_targets, list) and plan.target:
        if plan.target not in set(allowed_targets):
            errors.append(f"state={plan.state} 不允许 target={plan.target}（allowed={allowed_targets}）")

    # confidence 门槛
    if plan.state_confidence < 0.55:
        errors.append(f"state_confidence 过低：{plan.state_confidence}")
    if plan.action_confidence < 0.55 and not plan.done:
        errors.append(f"action_confidence 过低：{plan.action_confidence}")

    return (not errors, errors)


class VlmUiDriver:
    """本地 VLM 全控 UI driver（Ollama）。

    v1 行为（用户确认）：
    - 单次推理输出 `UI_PLAN_JSON`（包含 state + action + target + bbox_norm）。
    - 程序侧做硬校验；不合法时最多触发一次纠错重问。
    - 输入图片必须为 WebP(q=95)，不做 PNG/JPEG 兜底。

    性能策略（用户确认）：
    - 为了把单步推理时间压到可用范围，允许对“送入模型”的图片做 resize（降分辨率）。
    - 注意：resize 会丢失细节，但这是为了避免 Ollama 侧 `/api/chat` 在约 60s 左右硬超时返回 500。
    - 原始窗口截图（PNG）仍会落盘，便于复盘。
    """

    def __init__(self, *, ui: MacOSUIAutomation) -> None:
        self.ui = ui

        cfg = UI_AUTOMATION_CONFIG
        self.cfg = cfg
        self.ollama = OllamaClient(base_url=cfg.ollama_base_url, timeout_sec=cfg.vlm_timeout_sec)

        allow = set(cfg.vlm_allow_actions)
        self.allow_actions = sorted(allow or {"click", "type_text", "noop"})

        self.require_ui_change = bool(cfg.vlm_require_ui_change)
        self.playbook = _load_playbook_json()
        self.playbook_compact = _compact_playbook_for_prompt(self.playbook)

        # bbox 最小面积阈值（可后续配置化；当前以正确性为主，防止“角落碰碰运气”）
        self.bbox_area_min = 0.002

    async def _ensure_kugou_frontmost(self, *, step: str, debug: Dict[str, Any]) -> None:
        before = None
        after = None
        err = None

        try:
            before = await self.ui.get_frontmost_process_name()
        except Exception as e:
            err = str(e)

        try:
            await self.ui.activate_app("酷狗音乐")
        except Exception:
            pass

        try:
            await self.ui.set_process_frontmost("酷狗音乐")
        except Exception:
            pass

        await asyncio.sleep(0.12)

        try:
            after = await self.ui.get_frontmost_process_name()
        except Exception as e:
            err = str(e)

        evidence = {
            "step": str(step),
            "before": before,
            "after": after,
            "ok": str(after).strip() in set(KUGOU_APP_NAMES),
            "error": err,
        }
        debug.setdefault("frontmostChecks", []).append(evidence)

        if evidence["ok"] is not True:
            raise RuntimeError(f"前台应用不是酷狗，已中止点击以避免误操作：{evidence}")

    @staticmethod
    def _bbox_center_image_point(
        bbox_norm: Mapping[str, Any],
        *,
        image_size: Mapping[str, Any],
    ) -> Tuple[float, float]:
        iw = float(image_size.get("width") or 0.0)
        ih = float(image_size.get("height") or 0.0)
        if iw <= 0.0 or ih <= 0.0:
            raise RuntimeError("image_size 无效")

        x = float(bbox_norm.get("x") or 0.0)
        y = float(bbox_norm.get("y") or 0.0)
        w = float(bbox_norm.get("w") or 0.0)
        h = float(bbox_norm.get("h") or 0.0)

        cx = (x + w / 2.0) * iw
        cy = (y + h / 2.0) * ih
        return (float(cx), float(cy))

    def _build_system_prompt(self) -> str:
        pb = self.playbook_compact
        return (
            "你是一个本地运行的UI自动化助手。你会看到一张酷狗音乐窗口截图，需要决定下一步操作以完成任务。\n\n"
            "输出协议（强约束）：\n"
            "- 你必须只输出一行 JSON，并以前缀 UI_PLAN_JSON 开头。\n"
            "- 除 UI_PLAN_JSON 外，禁止输出任何解释文本。\n"
            "- bbox_norm 为 {x,y,w,h}，均为 0..1 的归一化比例（左上为原点），必须满足 x+w<=1 且 y+h<=1。\n"
            "- action 只能是：click / type_text / noop。\n"
            "- type_text 必须给 text；submit=true 表示需要回车提交。\n"
            "- 若已完成目标（已开始播放），请 done=true 且 action=noop。\n\n"
            "Playbook（必须遵守）：\n"
            f"- version={pb.get('version')}\n"
            f"- states={pb.get('states')}\n"
            f"- targets={pb.get('targets')}\n"
            f"- state_target_allow={pb.get('state_target_allow')}\n\n"
            "重要：state 决定允许的 target；不允许在错误 state 下编造 search_entry。\n"
        )

    def _build_user_payload(
        self,
        *,
        query: str,
        step: int,
        recent_steps: Sequence[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "task": "kugou_search_and_play",
            "query": str(query),
            "step": int(step),
            "recent_steps": list(recent_steps)[-6:],
            "notes": [
                "必须先判断当前 state，再给 action/target/bbox。",
                "如果侧边栏未处于音乐主界面（not_music_main），下一步必须先点击 sidebar_music。",
                "若搜索入口被遮挡，优先点击 back_button 退出遮挡。",
                "type_text 前会自动执行 Cmd+A + Delete 清空输入框。",
            ],
        }

    async def _ask_plan_once(
        self,
        *,
        query: str,
        step_idx: int,
        img_webp_b64: str,
        recent_steps: Sequence[Mapping[str, Any]],
        correction: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        system_prompt = self._build_system_prompt()
        user_payload = self._build_user_payload(query=query, step=step_idx, recent_steps=recent_steps)
        if correction:
            user_payload["correction"] = correction

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]

        result = await self.ollama.chat(
            model=self.cfg.vlm_model,
            messages=messages,
            images_base64=[img_webp_b64],
            stream=False,
            keep_alive=self.cfg.vlm_keep_alive,
            options={"temperature": float(self.cfg.vlm_temperature)},
        )

        return (str(result.content or ""), messages)

    async def run_kugou_search_play(
        self,
        *,
        query: str,
        debug: bool,
        dry_run: bool,
    ) -> Dict[str, Any]:
        if not str(query or "").strip():
            raise RuntimeError("VLM 模式 search 需要提供 query")

        debug_info: Dict[str, Any] = {
            "mode": "kugou_vlm_driver_plan_v1",
            "vlm": {
                "baseUrl": self.cfg.ollama_base_url,
                "model": self.cfg.vlm_model,
                "timeoutSec": self.cfg.vlm_timeout_sec,
                "keepAlive": self.cfg.vlm_keep_alive,
                "temperature": self.cfg.vlm_temperature,
                "maxSteps": self.cfg.vlm_max_steps,
                "webpQuality": 95,
                "bboxAreaMin": self.bbox_area_min,
            },
            "playbook": self.playbook_compact,
            "query": str(query),
        }

        warp_enabled = str(os.environ.get("VOICE_ASSISTANT_DEBUG_WARP") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "on",
        }
        debug_info["debugWarpEnabled"] = bool(warp_enabled)

        out_dir = _debug_dir()

        def _dump_json(tag: str, payload: Dict[str, Any]) -> Optional[str]:
            try:
                path = out_dir / f"kugou_vlm_{str(tag).strip() or 'debug'}_{_now_ms()}.json"
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                debug_info.setdefault("debugDumps", []).append(str(path))
                return str(path)
            except Exception as e:
                debug_info.setdefault("warnings", []).append(f"debug json 落盘失败（忽略）：{e}")
                return None

        await self.ui.ensure_accessibility_ready()
        await self._ensure_kugou_frontmost(step="kugou_vlm_init", debug=debug_info)

        init_cap = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag="kugou_vlm_init")
        debug_info.setdefault("captures", []).append({"step": "kugou_vlm_init", "capture": init_cap})

        no_change_streak = 0
        recent_steps: List[Dict[str, Any]] = []

        for step_idx in range(int(self.cfg.vlm_max_steps)):
            step_tag = f"kugou_vlm_step_{step_idx}"
            await self._ensure_kugou_frontmost(step=f"{step_tag}_frontmost", debug=debug_info)

            cap_before = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=f"{step_tag}_before")
            debug_info.setdefault("captures", []).append({"step": f"{step_tag}_before", "capture": cap_before})

            before_path = str(cap_before.get("screenshotPath") or "")
            if not before_path:
                _dump_json("error_missing_before_screenshot", {"step": step_tag, "debug": debug_info})
                raise RuntimeError("VLM driver 无法获取截图路径")

            before_hash = self.ui.file_sha256(before_path)

            # 送入模型的图片：先按最长边缩放（降低 VLM 视觉编码成本），再转 WebP(q=95)。
            # 原始窗口截图仍保留为 PNG 并落盘。
            scaled_png_path = ""
            webp_path = ""
            try:
                scaled_png_path = _sips_resize_png_max_side(
                    src_png=before_path,
                    out_dir=out_dir,
                    tag=step_tag,
                    max_side=int(self.cfg.vlm_image_max_side),
                )
                webp_path = _ensure_webp_q95_from_png(src_png=scaled_png_path, out_dir=out_dir, tag=step_tag)
            except Exception as e:
                msg = str(e).strip() or repr(e)
                _dump_json(
                    f"{step_tag}_webp_error",
                    {
                        "step": int(step_idx),
                        "error": msg,
                        "beforeScreenshotPath": before_path,
                        "beforeScreenshotSha256": before_hash,
                        "scaledPngPath": str(scaled_png_path or ""),
                        "maxSide": int(self.cfg.vlm_image_max_side),
                    },
                )
                raise RuntimeError(msg)

            img_b64 = _read_base64(webp_path)

            request_dump = {
                "step": int(step_idx),
                "model": self.cfg.vlm_model,
                "beforeScreenshotPath": str(before_path),
                "beforeScreenshotSha256": str(before_hash),
                "scaledPngPath": str(scaled_png_path),
                "scaledPngBytes": int(Path(scaled_png_path).stat().st_size) if Path(scaled_png_path).exists() else 0,
                "inputWebpPath": str(webp_path),
                "inputWebpBytes": int(Path(webp_path).stat().st_size) if Path(webp_path).exists() else 0,
                "maxSide": int(self.cfg.vlm_image_max_side),
                "recent_steps": recent_steps[-6:],
            }
            _dump_json(f"{step_tag}_request", request_dump)

            # 第一次询问
            try:
                response_text, messages = await self._ask_plan_once(
                    query=query,
                    step_idx=step_idx,
                    img_webp_b64=img_b64,
                    recent_steps=recent_steps,
                    correction=None,
                )
            except Exception as e:
                msg = str(e).strip() or repr(e)
                _dump_json(
                    f"{step_tag}_ollama_error",
                    {
                        "step": int(step_idx),
                        "error": msg,
                        "model": self.cfg.vlm_model,
                        "baseUrl": self.cfg.ollama_base_url,
                        "timeoutSec": self.cfg.vlm_timeout_sec,
                        "inputWebpPath": str(webp_path),
                        "inputWebpBytes": int(Path(webp_path).stat().st_size) if Path(webp_path).exists() else 0,
                        "maxSide": int(self.cfg.vlm_image_max_side),
                    },
                )
                raise RuntimeError(msg)

            _dump_json(f"{step_tag}_response", {"content": response_text, "messages": messages})

            try:
                plan, plan_raw = _parse_plan(response_text)
            except Exception as e:
                _dump_json(
                    f"{step_tag}_parse_error",
                    {
                        "step": int(step_idx),
                        "error": str(e),
                        "responseTail": response_text[-1200:],
                    },
                )
                raise RuntimeError(f"VLM 输出无法解析为 UI_PLAN_JSON：{e}")

            ok, errors = _validate_plan(
                plan,
                allow_actions=self.allow_actions,
                playbook=self.playbook,
                bbox_area_min=self.bbox_area_min,
            )

            # 不合法：触发一次纠错重问（单步最多一次）。
            if not ok:
                _dump_json(
                    f"{step_tag}_validate_fail",
                    {
                        "step": int(step_idx),
                        "errors": errors,
                        "planRaw": plan_raw,
                    },
                )

                correction = {
                    "invalid_reason": errors,
                    "previous_plan": plan_raw,
                    "require": "请严格遵守 playbook 的 state->target 约束，并重新输出 UI_PLAN_JSON。",
                }

                response_text2, messages2 = await self._ask_plan_once(
                    query=query,
                    step_idx=step_idx,
                    img_webp_b64=img_b64,
                    recent_steps=recent_steps,
                    correction=correction,
                )
                _dump_json(f"{step_tag}_response_retry", {"content": response_text2, "messages": messages2})

                try:
                    plan, plan_raw = _parse_plan(response_text2)
                except Exception as e:
                    raise RuntimeError(f"纠错重问仍无法解析 UI_PLAN_JSON：{e}")

                ok, errors = _validate_plan(
                    plan,
                    allow_actions=self.allow_actions,
                    playbook=self.playbook,
                    bbox_area_min=self.bbox_area_min,
                )
                if not ok:
                    _dump_json(
                        f"{step_tag}_validate_fail_retry",
                        {
                            "step": int(step_idx),
                            "errors": errors,
                            "planRaw": plan_raw,
                        },
                    )
                    raise RuntimeError(f"VLM 输出计划不合法（纠错后仍失败）：{errors}")

            step_debug: Dict[str, Any] = {
                "step": int(step_idx),
                "state": plan.state,
                "state_confidence": plan.state_confidence,
                "action": plan.action,
                "target": plan.target,
                "action_confidence": plan.action_confidence,
                "bbox_norm": plan.bbox_norm,
                "evidence": list(plan.evidence),
                "planRaw": plan_raw,
                "beforeSha256": before_hash,
                "inputWebpPath": str(webp_path),
            }

            if plan.done is True:
                step_debug["done"] = True
                debug_info.setdefault("steps", []).append(step_debug)
                recent_steps.append(
                    {
                        "step": int(step_idx),
                        "state": plan.state,
                        "action": plan.action,
                        "target": plan.target,
                        "done": True,
                    }
                )
                _dump_json(f"{step_tag}_done", step_debug)
                break

            if dry_run:
                step_debug["dryRun"] = True
                debug_info.setdefault("steps", []).append(step_debug)
                recent_steps.append(
                    {
                        "step": int(step_idx),
                        "state": plan.state,
                        "action": plan.action,
                        "target": plan.target,
                        "dryRun": True,
                    }
                )
                _dump_json(f"{step_tag}_dry_run", step_debug)
                continue

            click_debug = None

            if plan.action == "click":
                cx, cy = self._bbox_center_image_point(
                    plan.bbox_norm or {},
                    image_size=cap_before.get("imageSize") or {},
                )
                sx, sy = self.ui._to_screen_point_from_window_image_point(  # noqa: SLF001
                    float(cx),
                    float(cy),
                    window_bounds=cap_before.get("windowBounds") or {},
                    image_size=cap_before.get("imageSize") or {},
                )

                await self._ensure_kugou_frontmost(step=f"{step_tag}_preclick", debug=debug_info)
                if warp_enabled:
                    click_debug = await self.ui.click_at_debug(
                        float(sx),
                        float(sy),
                        clicks=1,
                        tag=f"{step_tag}_click",
                        warp_cursor=True,
                        settle_sec=0.03,
                        window_bounds=cap_before.get("windowBounds") or {},
                        image_size=cap_before.get("imageSize") or {},
                    )
                else:
                    await self.ui.click_at(float(sx), float(sy), clicks=1)
                    click_debug = {"targetPoint": {"x": float(sx), "y": float(sy)}, "warpCursor": False}

                await asyncio.sleep(0.18)

            elif plan.action == "type_text":
                cx, cy = self._bbox_center_image_point(
                    plan.bbox_norm or {},
                    image_size=cap_before.get("imageSize") or {},
                )
                sx, sy = self.ui._to_screen_point_from_window_image_point(  # noqa: SLF001
                    float(cx),
                    float(cy),
                    window_bounds=cap_before.get("windowBounds") or {},
                    image_size=cap_before.get("imageSize") or {},
                )

                await self._ensure_kugou_frontmost(step=f"{step_tag}_prefocus", debug=debug_info)
                await self.ui.click_at(float(sx), float(sy), clicks=1)
                await asyncio.sleep(0.12)

                try:
                    await self.ui.hotkey("a", modifiers=["command down"])
                    await self.ui.key_code(51)
                except Exception as e:
                    debug_info.setdefault("warnings", []).append(f"清空输入框失败（忽略）：{e}")

                await self.ui.type_text(plan.text)

                if plan.submit is True:
                    await self.ui.key_code(36)

                await asyncio.sleep(0.25)

            else:
                await asyncio.sleep(0.05)

            cap_after = await self.ui.screenshot_window(owner_names=KUGOU_APP_NAMES, tag=f"{step_tag}_after")
            debug_info.setdefault("captures", []).append({"step": f"{step_tag}_after", "capture": cap_after})

            after_path = str(cap_after.get("screenshotPath") or "")
            after_hash = self.ui.file_sha256(after_path) if after_path else ""

            same = bool(before_hash and after_hash and before_hash == after_hash)
            step_debug.update(
                {
                    "afterScreenshotPath": after_path,
                    "afterSha256": after_hash,
                    "uiSame": same,
                    "clickDebug": click_debug,
                }
            )
            debug_info.setdefault("steps", []).append(step_debug)
            _dump_json(f"{step_tag}_step", step_debug)

            recent_steps.append(
                {
                    "step": int(step_idx),
                    "state": plan.state,
                    "action": plan.action,
                    "target": plan.target,
                    "uiSame": same,
                }
            )

            if same and self.require_ui_change and (plan.idempotent_ok is not True) and plan.action != "noop":
                no_change_streak += 1
                debug_info.setdefault("warnings", []).append(
                    f"步骤未产生界面变化（require_ui_change=1）：step={step_idx}, action={plan.action}"
                )
                if no_change_streak >= 2:
                    _dump_json("error_no_ui_change", {"step": step_idx, "debug": debug_info})
                    raise RuntimeError("VLM 连续多步未产生界面变化，已中止以避免误操作")
            else:
                no_change_streak = 0

        if dry_run:
            return {
                "message": f"(dry-run) 已按 VLM 模式解析步骤（未执行点击/键入）：{query}",
                "debug": debug_info if debug else None,
            }

        return {
            "message": f"已按 VLM 模式执行酷狗搜索链路（未收到 done 信号）：{query}",
            "debug": debug_info if debug else None,
        }
