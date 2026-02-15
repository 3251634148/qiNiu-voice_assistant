#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""本地设备定位调试脚本（macOS CoreLocation）。

用途：
- 在不消耗任何联网 API 配额的前提下，验证 macOS CoreLocation 能否获取街区级位置。
- 输出经纬度、精度（米）以及街道/区/市等地址信息。

用法：
- `backend_py/.venv/bin/python test_scripts/debug_device_location.py`

说明：
- 首次运行可能触发系统定位授权弹窗，请允许。
- 调试产物会写入 `~/Documents/VoiceAssistant/ui_debug/<requestId>/`。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# 让脚本在未安装为 site-packages 的情况下也能导入 `backend_py/`
REPO_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(REPO_ROOT))

from backend_py.services.device_location_service import DeviceLocationService
from backend_py.services.network_tools_service import NetworkToolsService

import asyncio


def _debug_dir(request_id: str) -> Path:
    base = Path.home() / "Documents" / "VoiceAssistant" / "ui_debug" / str(request_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


def dump_debug_artifact(*, request_id: str, tag: str, payload: dict) -> str:
    out_dir = _debug_dir(request_id)
    ts = int(time.time() * 1000)
    out_path = out_dir / f"{tag}_{ts}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out_path)


def main() -> None:
    request_id = f"debug_device_location_{int(time.time() * 1000)}"
    os.environ["VOICE_ASSISTANT_DEBUG_RUN"] = request_id

    svc = DeviceLocationService()
    if not svc.is_available():
        raise SystemExit("CoreLocation 不可用：请确认已安装 pyobjc-framework-CoreLocation")

    tools = NetworkToolsService()

    async def _geo_lookup(lon_lat: str) -> dict:
        parts = [p.strip() for p in str(lon_lat or "").split(",")]
        if len(parts) != 2:
            return {"ok": False, "error": "invalid_lon_lat"}

        try:
            lon_f = float(parts[0])
            lat_f = float(parts[1])
        except Exception:
            return {"ok": False, "error": "invalid_lon_lat"}

        lon_lat_2dp = f"{lon_f:.2f},{lat_f:.2f}"
        try:
            geo = await tools.qweather_city_lookup(location=lon_lat_2dp, range_="cn", lang="zh", number=10)
        except Exception as e:
            return {"ok": False, "lonLat2dp": lon_lat_2dp, "error": str(e)}

        chosen = None
        if isinstance(geo, dict) and str(geo.get("code") or "") == "200":
            locs = geo.get("location")
            if isinstance(locs, list) and locs and isinstance(locs[0], dict):
                chosen = locs[0]

        return {"ok": bool(chosen), "lonLat2dp": lon_lat_2dp, "code": (geo or {}).get("code"), "chosen": chosen}

    results = []
    for i in range(2):
        started = time.time()
        r = svc.get_current_location(timeout_sec=12)
        elapsed_ms = int((time.time() - started) * 1000)

        geo_lookup = None
        if bool(r.ok) and isinstance(r.result, dict) and r.result.get("lon_lat"):
            try:
                geo_lookup = asyncio.run(_geo_lookup(str(r.result.get("lon_lat"))))
            except Exception as e:
                geo_lookup = {"ok": False, "error": f"geo_lookup_failed: {e}"}

        results.append(
            {
                "attempt": i + 1,
                "ok": bool(r.ok),
                "elapsedMs": elapsed_ms,
                "result": r.result,
                "geoLookup": geo_lookup,
                "error": r.error,
            }
        )

    payload = {
        "requestId": request_id,
        "results": results,
    }

    dump_debug_artifact(request_id=request_id, tag="debug_device_location", payload=payload)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nartifacts: ~/Documents/VoiceAssistant/ui_debug/{request_id}/")


if __name__ == "__main__":
    main()
