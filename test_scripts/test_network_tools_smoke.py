#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""联网信息工具 smoke 测试。

用法示例：
- 默认仅测试不依赖配额/密钥的功能（时间 + IP 解析）：
  `python test_scripts/test_network_tools_smoke.py`

- 按需选择测试项（避免消耗 API 调用次数）：
  - 只测时间：
    `python test_scripts/test_network_tools_smoke.py --tests time`
  - 测时间 + web_search：
    `python test_scripts/test_network_tools_smoke.py --tests time,search`
  - 全量（不推荐，消耗配额）：
    `python test_scripts/test_network_tools_smoke.py --tests all`

可选测试项：
- time: get_current_time
- ip: get_ip_location
- search: web_search（需要 SERPAPI_API_KEY）
- news: get_latest_news（需要 NEWSDATA_API_KEY）
- weather_now: get_weather_now（需要 QWeather JWT + host）
- weather_12h: get_weather_12h（需要 QWeather JWT + host）
- weather: 同时跑 weather_now + weather_12h

需要环境变量（仅当选择对应测试项时才会使用）：
- `SERPAPI_API_KEY`
- `NEWSDATA_API_KEY`

- QWeather JWT（推荐）
  - `QWEATHER_API_HOST`
  - `QWEATHER_JWT_SUB`
  - `QWEATHER_JWT_KID`
  - `QWEATHER_JWT_PRIVATE_KEY_PATH`
  - （可选）`QWEATHER_JWT_TTL_SECONDS`

说明：
- 该脚本不会把密钥写入日志或落盘。
- 调试产物会写入 `~/Documents/VoiceAssistant/ui_debug/<requestId>/`。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set

# 让脚本在未安装为 site-packages 的情况下也能导入 `backend_py/`
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend_py.services.network_tools_service import NetworkToolsService


def _env(name: str) -> str:
    return str(os.getenv(name, "") or "").strip()


def _parse_tests(value: str) -> Set[str]:
    raw = [x.strip() for x in str(value or "").split(",") if x.strip()]
    if not raw:
        return {"time", "ip"}

    lowered = {x.lower() for x in raw}
    if "all" in lowered:
        return {"time", "ip", "search", "news", "weather_now", "weather_12h"}

    out: Set[str] = set()
    for x in lowered:
        if x == "weather":
            out.update({"weather_now", "weather_12h"})
        else:
            out.add(x)

    return out


async def _run(*, tests: Set[str]) -> int:
    request_id = f"smoke_net_tools_{int(time.time() * 1000)}"
    os.environ["VOICE_ASSISTANT_DEBUG_RUN"] = request_id

    svc = NetworkToolsService()

    print(f"[smoke] requestId={request_id}")
    print(f"[smoke] tests={','.join(sorted(tests))}")

    r_ip = None

    if "time" in tests:
        print("\n[smoke] get_current_time")
        r = await svc.execute(name="get_current_time", arguments={}, request_id=request_id)
        print(r.content_for_model)

    if "ip" in tests:
        print("\n[smoke] get_ip_location")
        try:
            r_ip = await svc.execute(name="get_ip_location", arguments={}, request_id=request_id)
            print(r_ip.content_for_model)
        except Exception as e:
            print(f"get_ip_location failed (ignored): {e}")

    if "search" in tests:
        serp_key = _env("SERPAPI_API_KEY")
        if serp_key:
            print("\n[smoke] web_search")
            r_search = await svc.execute(
                name="web_search",
                arguments={"query": "北京 天气", "num": 3},
                request_id=request_id,
            )
            print(r_search.content_for_model)
        else:
            print("\n[smoke] web_search: skipped (SERPAPI_API_KEY missing)")

    if "news" in tests:
        news_key = _env("NEWSDATA_API_KEY")
        if news_key:
            print("\n[smoke] get_latest_news")
            r_news = await svc.execute(
                name="get_latest_news",
                arguments={"query": "科技", "limit": 3, "country": "cn", "language": "zh"},
                request_id=request_id,
            )
            print(r_news.content_for_model)
        else:
            print("\n[smoke] get_latest_news: skipped (NEWSDATA_API_KEY missing)")

    if "weather_now" in tests or "weather_12h" in tests:
        qweather_host = _env("QWEATHER_API_HOST")
        qweather_sub = _env("QWEATHER_JWT_SUB")
        qweather_kid = _env("QWEATHER_JWT_KID")
        qweather_priv = _env("QWEATHER_JWT_PRIVATE_KEY_PATH")

        if qweather_host and qweather_sub and qweather_kid and qweather_priv:
            # 优先尝试 ipinfo 返回的 lon_lat
            lon_lat: Optional[str] = None
            try:
                import json

                if r_ip is not None:
                    ip_obj = json.loads(r_ip.content_for_model)
                    lon_lat = ip_obj.get("lon_lat")
            except Exception:
                lon_lat = None

            if lon_lat:
                args: Dict[str, Any] = {"location": lon_lat, "lang": "zh", "unit": "m"}
            else:
                # 兜底：用城市名测试 geo lookup -> weather
                args = {"location": "北京", "lang": "zh", "unit": "m"}

            if "weather_now" in tests:
                print("\n[smoke] get_weather_now")
                r_weather = await svc.execute(name="get_weather_now", arguments=args, request_id=request_id)
                print(r_weather.content_for_model)

            if "weather_12h" in tests:
                print("\n[smoke] get_weather_12h")
                r_weather_12h = await svc.execute(name="get_weather_12h", arguments=args, request_id=request_id)
                print(r_weather_12h.content_for_model)
        else:
            print(
                "\n[smoke] weather skipped (QWEATHER_API_HOST/QWEATHER_JWT_SUB/QWEATHER_JWT_KID/"
                "QWEATHER_JWT_PRIVATE_KEY_PATH missing)"
            )

    print("\n[smoke] ok")
    print(f"[smoke] artifacts: ~/Documents/VoiceAssistant/ui_debug/{request_id}/")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tests",
        default="time,ip",
        help=(
            "Comma-separated tests to run. Default: time,ip. "
            "Available: time,ip,search,news,weather_now,weather_12h,weather,all"
        ),
    )
    args = parser.parse_args()

    tests = _parse_tests(args.tests)
    code = asyncio.run(_run(tests=tests))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
