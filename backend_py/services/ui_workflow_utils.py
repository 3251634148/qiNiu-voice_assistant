from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence


@dataclass(frozen=True)
class TextDigest:
    """用于在 debug 中记录文本而不落盘明文。"""

    length: int
    sha256: str


def get_ui_debug_dir() -> Path:
    """返回 ui_debug 落盘目录。

    规则：
    - 基础目录：~/Documents/VoiceAssistant/ui_debug/
    - 若设置环境变量 VOICE_ASSISTANT_DEBUG_RUN，则落盘到其子目录（做安全化）
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


def now_ms() -> int:
    return int(time.time() * 1000)


def dump_json(tag: str, payload: Dict[str, Any]) -> str:
    """将 JSON 产物写入 ui_debug，返回文件路径。"""

    out_dir = get_ui_debug_dir()
    ts = now_ms()
    safe_tag = re.sub(r"[^0-9a-zA-Z_.-]+", "_", str(tag or "artifact")).strip("_")
    safe_tag = safe_tag[:80] if safe_tag else "artifact"
    out_path = out_dir / f"{safe_tag}_{ts}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out_path)


def text_digest(text: str) -> TextDigest:
    value = str(text or "")
    h = hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()
    return TextDigest(length=len(value), sha256=h)


def pbcopy(text: str) -> None:
    """写入 macOS 剪贴板。"""

    subprocess.run(["pbcopy"], input=str(text or ""), text=True, check=False)


def pbpaste() -> str:
    """读取 macOS 剪贴板（尽力而为）。"""

    try:
        proc = subprocess.run(["pbpaste"], capture_output=True, text=True, check=False)
        return str(proc.stdout or "")
    except Exception:
        return ""


def run_cmd(cmd: Sequence[str], *, timeout_sec: float = 3.0) -> Dict[str, Any]:
    """执行命令并返回可落盘的结构化结果（不抛异常）。"""

    try:
        proc = subprocess.run(list(cmd), capture_output=True, text=True, timeout=float(timeout_sec), check=False)
        return {
            "cmd": list(cmd),
            "returncode": int(proc.returncode),
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
        }
    except Exception as e:
        return {"cmd": list(cmd), "error": str(e)}


def crop_png_with_sips(
    *,
    screenshot_path: str,
    roi_pixels: Dict[str, int],
    out_path: Path,
) -> bool:
    """用 sips 从整图裁剪 ROI，保持像素不变（尽力而为）。"""

    try:
        h = int(max(1, roi_pixels.get("height") or 1))
        w = int(max(1, roi_pixels.get("width") or 1))
        y = int(max(0, roi_pixels.get("y") or 0))
        x = int(max(0, roi_pixels.get("x") or 0))

        subprocess.run(
            [
                "sips",
                "-c",
                str(h),
                str(w),
                "--cropOffset",
                str(y),
                str(x),
                str(screenshot_path),
                "-o",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return out_path.exists()
    except Exception:
        return False


def roi_norm_to_pixels(
    roi: tuple[float, float, float, float],
    *,
    image_w: float,
    image_h: float,
) -> Dict[str, int]:
    rx, ry, rw, rh = roi
    return {
        "x": int(round(float(rx) * float(image_w))) if float(image_w) > 0 else 0,
        "y": int(round(float(ry) * float(image_h))) if float(image_h) > 0 else 0,
        "width": int(round(float(rw) * float(image_w))) if float(image_w) > 0 else 0,
        "height": int(round(float(rh) * float(image_h))) if float(image_h) > 0 else 0,
    }


def parse_roi_csv(value: str) -> Optional[tuple[float, float, float, float]]:
    raw = str(value or "").strip()
    if not raw:
        return None

    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        return None

    try:
        x = float(parts[0])
        y = float(parts[1])
        w = float(parts[2])
        h = float(parts[3])
    except Exception:
        return None

    def _clamp01(v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    x = _clamp01(x)
    y = _clamp01(y)
    w = _clamp01(w)
    h = _clamp01(h)
    w = max(0.0, min(1.0 - x, w))
    h = max(0.0, min(1.0 - y, h))
    if w <= 0.0 or h <= 0.0:
        return None

    return (x, y, w, h)


def roi_from_env(env_name: str, *, default: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    parsed = parse_roi_csv(os.environ.get(env_name, ""))
    if parsed:
        return parsed
    return tuple(float(x) for x in default)
