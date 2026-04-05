from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_id(value: str, *, limit: int = 120) -> str:
    safe = re.sub(r"[^0-9a-zA-Z_.-]+", "_", str(value or "")).strip("_")
    safe = safe[: int(limit)] if safe else ""
    return safe


def _perf_dir_for_request(request_id: str) -> Path:
    base = Path.home() / "Documents" / "VoiceAssistant" / "ui_debug"
    safe = _safe_id(request_id, limit=120)
    out = base / safe if safe else base
    out.mkdir(parents=True, exist_ok=True)
    return out


@dataclass
class PerfSpan:
    name: str
    startMs: int
    endMs: int
    durMs: int
    ok: bool = True
    error: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


class PerfRecorder:
    """请求级性能复盘记录器。

    设计目标：
    - 每个 requestId 对应一个固定文件：ui_debug/<requestId>/perf_summary.json
    - 允许在链路不同阶段多次写入（合并已有内容），最终形成完整复盘
    - 不落盘敏感明文（建议只写长度/哈希/枚举信息）
    """

    def __init__(self, *, request_id: str) -> None:
        self.request_id = str(request_id or "").strip()
        self.created_ms = _now_ms()
        self.meta: Dict[str, Any] = {"requestId": self.request_id}
        self.metrics: Dict[str, Any] = {}
        self.spans: List[PerfSpan] = []

        self._path = _perf_dir_for_request(self.request_id) / "perf_summary.json"
        self._load_existing()

    @property
    def path(self) -> Path:
        return self._path

    def _load_existing(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return

        if not isinstance(data, dict):
            return

        # 合并 meta/metrics（新值覆盖旧值）
        old_meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        old_metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        if isinstance(old_meta, dict):
            self.meta = {**old_meta, **self.meta}
        if isinstance(old_metrics, dict):
            self.metrics = {**old_metrics, **self.metrics}

        # 读取已有 spans
        old_spans = data.get("spans")
        if isinstance(old_spans, list):
            for s in old_spans:
                if not isinstance(s, dict):
                    continue
                try:
                    self.spans.append(
                        PerfSpan(
                            name=str(s.get("name") or ""),
                            startMs=int(s.get("startMs") or 0),
                            endMs=int(s.get("endMs") or 0),
                            durMs=int(s.get("durMs") or 0),
                            ok=bool(s.get("ok") is not False),
                            error=str(s.get("error") or ""),
                            meta=s.get("meta") if isinstance(s.get("meta"), dict) else {},
                        )
                    )
                except Exception:
                    continue

    def set_meta(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            if v is None:
                continue
            self.meta[str(k)] = v

    def set_metric(self, key: str, value: Any) -> None:
        k = str(key or "").strip()
        if not k:
            return
        self.metrics[k] = value

    def add_span(self, *, name: str, start_ms: int, end_ms: int, ok: bool = True, error: str = "", meta: Optional[Dict[str, Any]] = None) -> None:
        s = int(start_ms)
        e = int(end_ms)
        self.spans.append(
            PerfSpan(
                name=str(name or "").strip() or "span",
                startMs=s,
                endMs=e,
                durMs=max(0, e - s),
                ok=bool(ok),
                error=str(error or ""),
                meta=meta or {},
            )
        )

    def dump(self) -> str:
        obj = {
            "meta": {
                **self.meta,
                "updatedAtMs": _now_ms(),
            },
            "metrics": self.metrics,
            "spans": [
                {
                    "name": sp.name,
                    "startMs": int(sp.startMs),
                    "endMs": int(sp.endMs),
                    "durMs": int(sp.durMs),
                    "ok": bool(sp.ok),
                    "error": sp.error,
                    "meta": sp.meta,
                }
                for sp in self.spans
            ],
        }

        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)
        return str(self._path)
