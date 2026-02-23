from __future__ import annotations

import os
from dataclasses import dataclass
from typing import FrozenSet, Optional


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, *, min_value: int, max_value: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return int(default)
    try:
        v = int(raw)
    except Exception:
        return int(default)
    return int(max(min_value, min(max_value, v)))


def _env_float(name: str, default: float, *, min_value: float, max_value: float) -> float:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return float(default)
    try:
        v = float(raw)
    except Exception:
        return float(default)
    return float(max(min_value, min(max_value, v)))


def _env_str(name: str, default: str) -> str:
    v = str(os.getenv(name, default) or default)
    return v.strip() if v else str(default)


@dataclass(frozen=True)
class UiAutomationConfig:
    """UI 自动化配置。

    说明：
    - mode=ocr：使用现有 Vision OCR + ROI/状态机工作流（默认）。
    - mode=vlm：使用本地 Ollama VLM 全控模式（状态机驱动 click/type_text/noop）。
    """

    mode: str

    # Ollama
    ollama_base_url: str
    vlm_model: str
    vlm_timeout_sec: float
    vlm_keep_alive: str
    vlm_temperature: float

    # Driver limits
    vlm_max_steps: int
    vlm_image_max_side: int
    vlm_topk: int

    # Verification
    vlm_require_ui_change: bool

    # Allowed actions (defense-in-depth)
    vlm_allow_actions: FrozenSet[str]


def load_ui_automation_config() -> UiAutomationConfig:
    mode = _env_str("VOICE_ASSISTANT_UI_AUTOMATION_MODE", "ocr").lower()
    if mode not in {"ocr", "vlm"}:
        mode = "ocr"

    base_url = _env_str("VOICE_ASSISTANT_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    vlm_model = _env_str("VOICE_ASSISTANT_VLM_MODEL", "qwen3-vl:4b")

    timeout_sec = _env_float("VOICE_ASSISTANT_VLM_TIMEOUT_SEC", 8.0, min_value=1.0, max_value=120.0)
    keep_alive = _env_str("VOICE_ASSISTANT_VLM_KEEP_ALIVE", "10m")
    temperature = _env_float("VOICE_ASSISTANT_VLM_TEMPERATURE", 0.0, min_value=0.0, max_value=2.0)

    max_steps = _env_int("VOICE_ASSISTANT_VLM_MAX_STEPS", 10, min_value=1, max_value=30)
    image_max_side = _env_int("VOICE_ASSISTANT_VLM_IMAGE_MAX_SIDE", 512, min_value=128, max_value=2048)
    topk = _env_int("VOICE_ASSISTANT_VLM_TOPK", 3, min_value=1, max_value=8)

    require_ui_change = _env_bool("VOICE_ASSISTANT_VLM_REQUIRE_UI_CHANGE", True)

    allow_actions_raw = _env_str("VOICE_ASSISTANT_VLM_ALLOW_ACTIONS", "click,type_text,noop")
    allow_actions = {x.strip() for x in allow_actions_raw.split(",") if x.strip()}
    allow_actions = allow_actions or {"click", "type_text", "noop"}

    return UiAutomationConfig(
        mode=mode,
        ollama_base_url=base_url,
        vlm_model=vlm_model,
        vlm_timeout_sec=float(timeout_sec),
        vlm_keep_alive=keep_alive,
        vlm_temperature=float(temperature),
        vlm_max_steps=int(max_steps),
        vlm_image_max_side=int(image_max_side),
        vlm_topk=int(topk),
        vlm_require_ui_change=bool(require_ui_change),
        vlm_allow_actions=frozenset(allow_actions),
    )


# 单例配置：在进程生命周期内保持一致（如需热更新，可改为每次读取）。
UI_AUTOMATION_CONFIG = load_ui_automation_config()
