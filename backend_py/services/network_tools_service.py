from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except Exception:  # pragma: no cover
    serialization = None
    Ed25519PrivateKey = None

from backend_py.config import settings


@dataclass(frozen=True)
class NetworkToolResult:
    """网络工具执行结果。

    `content_for_model` 必须是字符串（用于 OpenAI tool message）。
    `debug_payload` 用于落盘证据与排障（不应包含任何密钥）。
    """

    content_for_model: str
    debug_payload: Dict[str, Any]


class NetworkToolsService:
    """联网信息工具集合（MCP Client 执行端）。

    设计目标：
    - 由 Qwen 决策是否调用工具（tool_calls），本服务负责具体执行并返回结果。
    - 所有请求/响应摘要都应落盘到 `ui_debug/<requestId>/`，便于复现与排障。
    - 严禁在日志或落盘里写入 API Key。

    本服务提供的工具：
    - get_current_time
    - web_search (SerpAPI)
    - get_latest_news (newsdata.io)
    - get_ip_location (ipinfo widget demo)
    - get_weather_now (QWeather)
    - get_weather_12h (QWeather)
    """

    DEFAULT_GOOGLE_DOMAIN = "google.com.hk"
    DEFAULT_GL = "cn"
    DEFAULT_HL = "zh-cn"

    DEFAULT_NEWS_COUNTRY = "cn"
    DEFAULT_NEWS_LANGUAGE = "zh"

    DEFAULT_WEATHER_LANG = "zh"
    DEFAULT_WEATHER_UNIT = "m"

    def __init__(self) -> None:
        self._http_timeout = 25.0

        # QWeather JWT 缓存（减少频繁读私钥/签名开销）。
        self._qweather_jwt_token: Optional[str] = None
        self._qweather_jwt_exp: int = 0
        self._qweather_ed25519_key: Any = None
        self._qweather_ed25519_key_path: str = ""

    @staticmethod
    def is_network_tool(name: str) -> bool:
        return name in {
            "get_current_time",
            "web_search",
            "get_latest_news",
            "get_ip_location",
            "get_weather_now",
            "get_weather_12h",
        }

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """返回可注入给 Qwen 的 tools schema。"""

        return [
            {
                "name": "get_current_time",
                "description": "当你想知道现在的本地时间时非常有用。",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "get_ip_location",
                "description": "基于当前公网 IP 查询城市/经纬度/时区信息。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ip": {
                            "type": "string",
                            "description": "可选：指定要查询的 IP；不填则查询当前公网 IP。",
                        },
                    },
                },
            },
            {
                "name": "web_search",
                "description": "互联网搜索（SerpAPI / Google）。用于获取最新信息、新闻线索、网页来源。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词"},
                        "location": {
                            "type": "string",
                            "description": "可选：地理位置（用于本土化/本地结果增强），例如 Shenzhen, Guangdong, China",
                        },
                        "google_domain": {
                            "type": "string",
                            "description": "可选：Google 域名，默认 google.com.hk",
                        },
                        "gl": {"type": "string", "description": "可选：国家代码，两位，例如 cn"},
                        "hl": {"type": "string", "description": "可选：语言代码，例如 zh-cn"},
                        "safe": {"type": "string", "description": "可选：安全过滤，active/off"},
                        "tbs": {
                            "type": "string",
                            "description": "可选：高级筛选参数（例如限定时间范围），直接透传给 SerpAPI。",
                        },
                        "num": {"type": "number", "description": "可选：返回条数上限（默认 5，最大 10）"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "get_latest_news",
                "description": "查询最新新闻（newsdata.io）。适合获取近期新闻摘要与来源链接。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "可选：新闻关键词（不填则返回最新）"},
                        "country": {"type": "string", "description": "可选：国家代码，默认 cn"},
                        "language": {"type": "string", "description": "可选：语言代码，默认 zh"},
                        "limit": {"type": "number", "description": "可选：返回条数上限（默认 5，最大 10）"},
                    },
                },
            },
            {
                "name": "get_weather_now",
                "description": "查询当前天气（QWeather /v7/weather/now）。支持城市名/区县名（会先通过 Geo API 解析 LocationID）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "必选：地区名称（如 北京/深圳龙华/chaoyang）、LocationID、或 ‘经度,纬度’（十进制，最多四位小数），例如 101010100 或 116.41,39.92",
                        },
                        "adm": {
                            "type": "string",
                            "description": "可选：上级行政区（用于排除重名），例如 beijing/黑龙江",
                        },
                        "range": {"type": "string", "description": "可选：搜索范围（ISO 3166 国家码），默认 cn"},
                        "number": {"type": "number", "description": "可选：Geo 返回条数 1-20，默认 5"},
                        "lang": {"type": "string", "description": "可选：多语言，默认 zh"},
                        "unit": {"type": "string", "description": "可选：单位，m=公制（默认）/i=英制"},
                    },
                    "required": ["location"],
                },
            },
            {
                "name": "get_weather_12h",
                "description": "查询未来 12 小时天气预报（基于 QWeather /v7/weather/24h，截取前 12 小时）。用于判断未来是否降雨、转凉等趋势。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "必选：地区名称（如 北京/深圳龙华/chaoyang）、LocationID、或 ‘经度,纬度’（十进制，最多四位小数）",
                        },
                        "adm": {
                            "type": "string",
                            "description": "可选：上级行政区（用于排除重名），例如 beijing/黑龙江",
                        },
                        "range": {"type": "string", "description": "可选：搜索范围（ISO 3166 国家码），默认 cn"},
                        "number": {"type": "number", "description": "可选：Geo 返回条数 1-20，默认 5"},
                        "lang": {"type": "string", "description": "可选：多语言，默认 zh"},
                        "unit": {"type": "string", "description": "可选：单位，m=公制（默认）/i=英制"},
                    },
                    "required": ["location"],
                },
            },
        ]

    async def execute(self, *, name: str, arguments: Dict[str, Any], request_id: str) -> NetworkToolResult:
        started_ms = int(time.time() * 1000)

        try:
            if name == "get_current_time":
                return self._get_current_time(started_ms=started_ms)

            if name == "get_ip_location":
                ip = str(arguments.get("ip") or "").strip() or None
                return await self._get_ip_location(ip=ip, started_ms=started_ms)

            if name == "web_search":
                query = str(arguments.get("query") or "").strip()
                if not query:
                    raise ValueError("query 不能为空")

                num = int(arguments.get("num") or 5)
                num = max(1, min(10, num))

                google_domain = str(arguments.get("google_domain") or self.DEFAULT_GOOGLE_DOMAIN).strip()
                gl = str(arguments.get("gl") or self.DEFAULT_GL).strip()
                hl = str(arguments.get("hl") or self.DEFAULT_HL).strip()
                safe = str(arguments.get("safe") or "active").strip()
                tbs = str(arguments.get("tbs") or "").strip() or None
                location = str(arguments.get("location") or "").strip() or None

                return await self._web_search(
                    query=query,
                    google_domain=google_domain,
                    gl=gl,
                    hl=hl,
                    safe=safe,
                    tbs=tbs,
                    location=location,
                    num=num,
                    started_ms=started_ms,
                )

            if name == "get_latest_news":
                query = str(arguments.get("query") or "").strip() or None
                country = str(arguments.get("country") or self.DEFAULT_NEWS_COUNTRY).strip()
                language = str(arguments.get("language") or self.DEFAULT_NEWS_LANGUAGE).strip()
                limit = int(arguments.get("limit") or 5)
                limit = max(1, min(10, limit))

                return await self._get_latest_news(
                    query=query,
                    country=country,
                    language=language,
                    limit=limit,
                    started_ms=started_ms,
                )

            if name == "get_weather_now":
                location = str(arguments.get("location") or "").strip()
                if not location:
                    raise ValueError("location 不能为空")

                adm = str(arguments.get("adm") or "").strip() or None
                range_ = str(arguments.get("range") or self.DEFAULT_GL).strip() or "cn"
                try:
                    number = int(arguments.get("number") or 5)
                except Exception:
                    number = 5
                number = max(1, min(20, number))

                lang = str(arguments.get("lang") or self.DEFAULT_WEATHER_LANG).strip()
                unit = str(arguments.get("unit") or self.DEFAULT_WEATHER_UNIT).strip()

                return await self._get_weather_now(
                    location=location,
                    adm=adm,
                    range_=range_,
                    number=number,
                    lang=lang,
                    unit=unit,
                    started_ms=started_ms,
                )

            if name == "get_weather_12h":
                location = str(arguments.get("location") or "").strip()
                if not location:
                    raise ValueError("location 不能为空")

                adm = str(arguments.get("adm") or "").strip() or None
                range_ = str(arguments.get("range") or self.DEFAULT_GL).strip() or "cn"
                try:
                    number = int(arguments.get("number") or 5)
                except Exception:
                    number = 5
                number = max(1, min(20, number))

                lang = str(arguments.get("lang") or self.DEFAULT_WEATHER_LANG).strip()
                unit = str(arguments.get("unit") or self.DEFAULT_WEATHER_UNIT).strip()

                return await self._get_weather_12h(
                    location=location,
                    adm=adm,
                    range_=range_,
                    number=number,
                    lang=lang,
                    unit=unit,
                    started_ms=started_ms,
                )

            raise ValueError(f"未知网络工具: {name}")

        finally:
            # 这里不落盘；由上层（ConversationController）统一落盘，保证 requestId 关联一致。
            _ = request_id

    @staticmethod
    def _debug_dir(request_id: str) -> Path:
        base = Path.home() / "Documents" / "VoiceAssistant" / "ui_debug"
        safe = re.sub(r"[^0-9a-zA-Z_.-]+", "_", str(request_id or "")).strip("_")
        safe = safe[:120] if safe else ""
        out = base / safe if safe else base
        out.mkdir(parents=True, exist_ok=True)
        return out

    @staticmethod
    def dump_debug_artifact(*, request_id: str, tag: str, payload: Dict[str, Any]) -> str:
        out_dir = NetworkToolsService._debug_dir(request_id)
        ts = int(time.time() * 1000)
        out_path = out_dir / f"{tag}_{ts}.json"
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(out_path)

    @staticmethod
    def _truncate_text(text: str, *, limit: int = 800) -> str:
        t = str(text or "")
        if len(t) <= limit:
            return t
        return t[:limit] + "…"

    @staticmethod
    def _get_current_time(*, started_ms: int) -> NetworkToolResult:
        now = datetime.now().astimezone()
        text = now.strftime("%Y-%m-%d %H:%M:%S %Z")
        payload = {
            "tool": "get_current_time",
            "ok": True,
            "startedMs": started_ms,
            "finishedMs": int(time.time() * 1000),
            "result": {"localTime": text},
        }
        return NetworkToolResult(content_for_model=json.dumps(payload["result"], ensure_ascii=False), debug_payload=payload)

    async def _web_search(
        self,
        *,
        query: str,
        google_domain: str,
        gl: str,
        hl: str,
        safe: str,
        tbs: Optional[str],
        location: Optional[str],
        num: int,
        started_ms: int,
    ) -> NetworkToolResult:
        if not settings.serpapi_api_key:
            raise RuntimeError("未配置 SERPAPI_API_KEY")

        params: Dict[str, Any] = {
            "engine": "google",
            "q": query,
            "google_domain": google_domain,
            "gl": gl,
            "hl": hl,
            "api_key": settings.serpapi_api_key,
            "safe": safe,
        }
        if location:
            params["location"] = location
        if tbs:
            params["tbs"] = tbs

        url = "https://serpapi.com/search.json"

        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        organic = data.get("organic_results") if isinstance(data, dict) else None
        items: List[Dict[str, Any]] = []
        if isinstance(organic, list):
            for r in organic[:num]:
                if not isinstance(r, dict):
                    continue
                items.append(
                    {
                        "title": self._truncate_text(r.get("title") or "", limit=120),
                        "url": r.get("link") or "",
                        "snippet": self._truncate_text(r.get("snippet") or "", limit=240),
                        "date": r.get("date") or None,
                        "source": (r.get("about_this_result") or {}).get("source", {}).get("description")
                        if isinstance(r.get("about_this_result"), dict)
                        else None,
                    }
                )

        result_for_model = {
            "query": query,
            "results": items,
        }

        payload = {
            "tool": "web_search",
            "ok": True,
            "startedMs": started_ms,
            "finishedMs": int(time.time() * 1000),
            "request": {
                "engine": "google",
                "q": query,
                "google_domain": google_domain,
                "gl": gl,
                "hl": hl,
                "location": location,
                "safe": safe,
                "tbs": tbs,
                "num": num,
            },
            "result": result_for_model,
        }
        return NetworkToolResult(content_for_model=json.dumps(result_for_model, ensure_ascii=False), debug_payload=payload)

    async def _get_latest_news(
        self,
        *,
        query: Optional[str],
        country: str,
        language: str,
        limit: int,
        started_ms: int,
    ) -> NetworkToolResult:
        if not settings.newsdata_api_key:
            raise RuntimeError("未配置 NEWSDATA_API_KEY")

        params: Dict[str, Any] = {
            "apikey": settings.newsdata_api_key,
            "country": country,
            "language": language,
        }
        if query:
            params["q"] = query

        url = "https://newsdata.io/api/1/latest"
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        raw_results = data.get("results") if isinstance(data, dict) else None
        items: List[Dict[str, Any]] = []
        if isinstance(raw_results, list):
            for r in raw_results[:limit]:
                if not isinstance(r, dict):
                    continue
                items.append(
                    {
                        "title": self._truncate_text(r.get("title") or "", limit=140),
                        "url": r.get("link") or "",
                        "description": self._truncate_text(r.get("description") or "", limit=260),
                        "pubDate": r.get("pubDate") or None,
                        "source": r.get("source_id") or r.get("source_name") or None,
                    }
                )

        result_for_model = {
            "query": query,
            "country": country,
            "language": language,
            "results": items,
        }

        payload = {
            "tool": "get_latest_news",
            "ok": True,
            "startedMs": started_ms,
            "finishedMs": int(time.time() * 1000),
            "request": {"query": query, "country": country, "language": language, "limit": limit},
            "result": result_for_model,
        }
        return NetworkToolResult(content_for_model=json.dumps(result_for_model, ensure_ascii=False), debug_payload=payload)

    async def _get_ip_location(self, *, ip: Optional[str], started_ms: int) -> NetworkToolResult:
        used_ip = ip
        ip_lookup_error = None

        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            # 优先使用 ipinfo 标准端点（更稳定），若不可用再回退 widget demo。
            if not used_ip:
                try:
                    resp_std = await client.get("https://ipinfo.io/json")
                    if resp_std.status_code == 200:
                        std = resp_std.json()
                        if isinstance(std, dict):
                            used_ip = str(std.get("ip") or "").strip() or None
                            if used_ip:
                                data_obj = std
                                data = {"input": used_ip, "data": data_obj}
                            else:
                                data = None
                        else:
                            data = None
                    else:
                        data = None
                except Exception as e:
                    ip_lookup_error = str(e)
                    data = None
            else:
                data = None

            if data is None and not used_ip:
                # 尝试 widget demo 的“无参模式”（如果支持会返回当前 IP）。
                try:
                    resp = await client.get("https://ipinfo.io/widget/demo/")
                    if resp.status_code == 200:
                        data0 = resp.json()
                        if isinstance(data0, dict) and isinstance((data0.get("data") or {}).get("ip"), str):
                            used_ip = str((data0.get("data") or {}).get("ip") or "").strip() or None
                except Exception as e:
                    ip_lookup_error = str(e)

            if data is None and not used_ip:
                # 兜底：用多个公网 IP 获取源，降低偶发失败概率。
                last_err = None
                for url, params in [
                    ("https://api.ipify.org", {"format": "json"}),
                    ("https://ifconfig.co/json", None),
                ]:
                    try:
                        resp_ip = await client.get(url, params=params)
                        resp_ip.raise_for_status()
                        ip_json = resp_ip.json()
                        used_ip = str((ip_json or {}).get("ip") or "").strip() or None
                        if used_ip:
                            break
                    except Exception as e:
                        last_err = str(e)

                if not used_ip:
                    raise RuntimeError(f"获取公网 IP 失败: {last_err or ''}")

            if data is None:
                # 回退：使用 widget demo 查询指定 IP。
                resp = await client.get(f"https://ipinfo.io/widget/demo/{used_ip}")
                resp.raise_for_status()
                data = resp.json()

        data_obj = data.get("data") if isinstance(data, dict) else None
        if not isinstance(data_obj, dict):
            raise RuntimeError("ipinfo 返回格式异常")

        city = str(data_obj.get("city") or "").strip() or None
        region = str(data_obj.get("region") or "").strip() or None
        country = str(data_obj.get("country") or "").strip() or None
        loc = str(data_obj.get("loc") or "").strip() or None
        timezone = str(data_obj.get("timezone") or "").strip() or None

        # loc: "lat,lon" -> 返回给模型时同时给出 (lon,lat) 便于天气查询。
        lon_lat = None
        if loc and "," in loc:
            parts = [p.strip() for p in loc.split(",")]
            if len(parts) == 2:
                lat_str, lon_str = parts
                lon_lat = self._normalize_lon_lat(lon=lon_str, lat=lat_str)

        result_for_model = {
            "ip": used_ip,
            "city": city,
            "region": region,
            "country": country,
            "loc": loc,
            "lon_lat": lon_lat,
            "timezone": timezone,
        }

        payload = {
            "tool": "get_ip_location",
            "ok": True,
            "startedMs": started_ms,
            "finishedMs": int(time.time() * 1000),
            "request": {"ip": ip},
            "result": result_for_model,
        }
        if ip_lookup_error:
            payload["warnings"] = [f"ipinfo 无参模式失败（已兜底 ipify）：{ip_lookup_error}"]

        return NetworkToolResult(content_for_model=json.dumps(result_for_model, ensure_ascii=False), debug_payload=payload)

    async def _geocode_city_to_lon_lat(self, *, city_name: str, started_ms: int) -> str:
        """将城市名解析为 "lon,lat"。

        优先使用 SerpAPI 的 `local_map.gps_coordinates`，因为结构化且稳定。
        若无法解析，则抛出异常。
        """

        if not settings.serpapi_api_key:
            raise RuntimeError("未配置 SERPAPI_API_KEY（无法根据城市名解析经纬度）")

        query = str(city_name or "").strip()
        if not query:
            raise ValueError("city_name 不能为空")

        params: Dict[str, Any] = {
            "engine": "google",
            "q": query,
            "google_domain": self.DEFAULT_GOOGLE_DOMAIN,
            "gl": self.DEFAULT_GL,
            "hl": self.DEFAULT_HL,
            "api_key": settings.serpapi_api_key,
            "safe": "active",
        }

        url = "https://serpapi.com/search.json"
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        local_map = data.get("local_map") if isinstance(data, dict) else None
        gps = (local_map or {}).get("gps_coordinates") if isinstance(local_map, dict) else None
        if isinstance(gps, dict):
            lat = gps.get("latitude")
            lon = gps.get("longitude")
            if lat is not None and lon is not None:
                lon_lat = self._normalize_lon_lat(lon=str(lon), lat=str(lat))
                return lon_lat

        raise RuntimeError(f"无法从 SerpAPI 结果中解析经纬度（city={query}）")

    @staticmethod
    def _base64url_encode(raw: bytes) -> str:
        """Base64URL 编码（无 padding）。"""

        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def _load_ed25519_private_key(self, *, private_key_path: str) -> Any:
        if serialization is None or Ed25519PrivateKey is None:
            raise RuntimeError("缺少依赖 cryptography，无法生成 QWeather JWT（Ed25519）")

        p = Path(str(private_key_path or "").strip()).expanduser()
        if not p.exists():
            raise RuntimeError(f"QWeather JWT 私钥文件不存在：{p}")

        key_obj = serialization.load_pem_private_key(p.read_bytes(), password=None)
        if not isinstance(key_obj, Ed25519PrivateKey):
            raise RuntimeError("QWeather JWT 私钥类型不匹配：需要 Ed25519 私钥")

        return key_obj

    def _get_qweather_jwt_token(self) -> str:
        """获取 QWeather JWT Token（EdDSA）。

        规则：
        - header: {"alg":"EdDSA","kid":kid}
        - payload: {"sub":kid,"iat":now-30,"exp":iat+ttl}
        - 仅允许 Base64URL（无 padding）
        """

        sub = str(getattr(settings, "qweather_jwt_sub", "") or "").strip()
        kid = str(getattr(settings, "qweather_jwt_kid", "") or "").strip()
        private_key_path = str(getattr(settings, "qweather_jwt_private_key_path", "") or "").strip()
        ttl = int(getattr(settings, "qweather_jwt_ttl_seconds", 3600) or 3600)

        if not sub:
            raise RuntimeError("未配置 QWEATHER_JWT_SUB")
        if not kid:
            raise RuntimeError("未配置 QWEATHER_JWT_KID")
        if not private_key_path:
            raise RuntimeError("未配置 QWEATHER_JWT_PRIVATE_KEY_PATH")

        ttl = max(60, min(86400, ttl))

        now = int(time.time())
        # 允许 30 秒时钟误差：iat 取当前时间前 30 秒。
        iat = max(0, now - 30)
        exp = iat + ttl

        # 缓存：若 token 仍有效（预留 60 秒裕量），则复用。
        if self._qweather_jwt_token and (now + 60) < int(self._qweather_jwt_exp or 0):
            return self._qweather_jwt_token

        if not self._qweather_ed25519_key or self._qweather_ed25519_key_path != private_key_path:
            self._qweather_ed25519_key = self._load_ed25519_private_key(private_key_path=private_key_path)
            self._qweather_ed25519_key_path = private_key_path

        header_obj = {"alg": "EdDSA", "kid": kid}
        payload_obj = {"sub": sub, "iat": iat, "exp": exp}

        header_json = json.dumps(header_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        payload_json = json.dumps(payload_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        header_b64 = self._base64url_encode(header_json)
        payload_b64 = self._base64url_encode(payload_json)

        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        signature = self._qweather_ed25519_key.sign(signing_input)
        signature_b64 = self._base64url_encode(signature)

        token = f"{header_b64}.{payload_b64}.{signature_b64}"
        self._qweather_jwt_token = token
        self._qweather_jwt_exp = exp
        return token

    async def _qweather_get(self, *, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if not settings.qweather_api_host:
            raise RuntimeError("未配置 QWEATHER_API_HOST")

        url = f"https://{settings.qweather_api_host}{path}"

        headers: Dict[str, str] = {}
        jwt_sub = str(getattr(settings, "qweather_jwt_sub", "") or "").strip()
        jwt_kid = str(getattr(settings, "qweather_jwt_kid", "") or "").strip()
        jwt_path = str(getattr(settings, "qweather_jwt_private_key_path", "") or "").strip()

        if jwt_sub and jwt_kid and jwt_path:
            headers["Authorization"] = f"Bearer {self._get_qweather_jwt_token()}"
        elif settings.qweather_api_key:
            # 兼容旧版 Key 模式（不推荐）。
            headers["Authorization"] = f"Bearer {settings.qweather_api_key}"
        else:
            raise RuntimeError(
                "未配置 QWeather 鉴权：请设置 QWEATHER_JWT_KID/QWEATHER_JWT_PRIVATE_KEY_PATH（推荐）"
                " 或 QWEATHER_API_KEY（兼容）"
            )

        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            try:
                resp = await client.get(url, headers=headers, params=params)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                status = getattr(e.response, "status_code", None)
                text = ""
                try:
                    text = e.response.text or ""
                except Exception:
                    text = ""
                tail = self._truncate_text(text, limit=800)
                raise RuntimeError(f"QWeather 请求失败（HTTP {status}）：{tail}")

    async def qweather_city_lookup(
        self,
        *,
        location: str,
        adm: Optional[str] = None,
        range_: str = "cn",
        number: int = 10,
        lang: str = "zh",
    ) -> Dict[str, Any]:
        """QWeather Geo：城市查询（GET /geo/v2/city/lookup）。

        说明：
        - 该接口支持 `lon,lat` 或城市名模糊查询。
        - 这里返回原始 JSON；上层可按 rank 选择第一条。
        """

        return await self._qweather_city_lookup(
            location=location,
            adm=adm,
            range_=range_,
            number=number,
            lang=lang,
        )

    async def _qweather_city_lookup(
        self,
        *,
        location: str,
        adm: Optional[str],
        range_: str,
        number: int,
        lang: str,
    ) -> Dict[str, Any]:
        """QWeather Geo：城市查询（GET /geo/v2/city/lookup）。

        返回原始 JSON（已是裁剪前的结构），上层会挑选 rank 更高的第一条。
        """

        params: Dict[str, Any] = {
            "location": location,
            "range": range_ or "cn",
            "number": int(number),
            "lang": lang,
        }
        if adm:
            params["adm"] = adm

        return await self._qweather_get(path="/geo/v2/city/lookup", params=params)

    async def _resolve_qweather_location(
        self,
        *,
        location: str,
        adm: Optional[str],
        range_: str,
        number: int,
        lang: str,
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        """将输入 location 解析为 QWeather 可用的 LocationID 或 lon,lat。

        - 若输入为纯数字：视为 LocationID
        - 若输入为 lon,lat：直接使用
        - 若输入为文字：先走 Geo lookup 获取 LocationID

        返回：
        - normalized_location: LocationID 或 lon,lat
        - chosen_geo: Geo lookup 选中的条目摘要（用于 debug/可观测性）
        """

        normalized_location = self._normalize_location(location)
        chosen_geo = None

        if normalized_location and (not normalized_location.isdigit()) and "," not in normalized_location:
            geo = await self._qweather_city_lookup(
                location=normalized_location,
                adm=adm,
                range_=range_,
                number=number,
                lang=lang,
            )
            locations = geo.get("location") if isinstance(geo, dict) else None
            if isinstance(locations, list) and locations:
                first = locations[0]
                if isinstance(first, dict) and isinstance(first.get("id"), str) and first.get("id"):
                    chosen_geo = {
                        "input": {
                            "location": normalized_location,
                            "adm": adm,
                            "range": range_,
                            "number": number,
                            "lang": lang,
                        },
                        "chosen": {
                            "name": first.get("name"),
                            "id": first.get("id"),
                            "adm1": first.get("adm1"),
                            "adm2": first.get("adm2"),
                            "country": first.get("country"),
                            "lat": first.get("lat"),
                            "lon": first.get("lon"),
                            "rank": first.get("rank"),
                            "fxLink": first.get("fxLink"),
                        },
                    }
                    normalized_location = str(first.get("id"))

            if not normalized_location.isdigit():
                raise RuntimeError("城市查询失败：未解析到有效的 LocationID")

        return normalized_location, chosen_geo

    async def _get_weather_now(
        self,
        *,
        location: str,
        adm: Optional[str],
        range_: str,
        number: int,
        lang: str,
        unit: str,
        started_ms: int,
    ) -> NetworkToolResult:
        normalized_location, chosen_geo = await self._resolve_qweather_location(
            location=location,
            adm=adm,
            range_=range_,
            number=number,
            lang=lang,
        )

        data = await self._qweather_get(
            path="/v7/weather/now",
            params={"location": normalized_location, "lang": lang, "unit": unit},
        )

        result_for_model = {
            "location": normalized_location,
            "code": (data or {}).get("code"),
            "updateTime": (data or {}).get("updateTime"),
            "now": (data or {}).get("now"),
            "fxLink": (data or {}).get("fxLink"),
            "refer": (data or {}).get("refer"),
        }

        auth_mode = (
            "jwt"
            if (
                getattr(settings, "qweather_jwt_sub", "")
                and getattr(settings, "qweather_jwt_kid", "")
                and getattr(settings, "qweather_jwt_private_key_path", "")
            )
            else "api_key"
        )
        payload = {
            "tool": "get_weather_now",
            "ok": True,
            "startedMs": started_ms,
            "finishedMs": int(time.time() * 1000),
            "request": {
                "location": normalized_location,
                "lang": lang,
                "unit": unit,
                "adm": adm,
                "range": range_,
                "number": number,
            },
            "auth": {
                "mode": auth_mode,
                "sub": str(getattr(settings, "qweather_jwt_sub", "") or "").strip() or None,
                "kid": str(getattr(settings, "qweather_jwt_kid", "") or "").strip() or None,
            },
            "geoLookup": chosen_geo,
            "result": result_for_model,
        }
        return NetworkToolResult(content_for_model=json.dumps(result_for_model, ensure_ascii=False), debug_payload=payload)

    async def _get_weather_12h(
        self,
        *,
        location: str,
        adm: Optional[str],
        range_: str,
        number: int,
        lang: str,
        unit: str,
        started_ms: int,
    ) -> NetworkToolResult:
        normalized_location, chosen_geo = await self._resolve_qweather_location(
            location=location,
            adm=adm,
            range_=range_,
            number=number,
            lang=lang,
        )

        data = await self._qweather_get(
            path="/v7/weather/24h",
            params={"location": normalized_location, "lang": lang, "unit": unit},
        )

        # QWeather 最小粒度为 24h；只取前 12 条（前 12 小时）给模型，减少 token 消耗。
        hourly_all = (data or {}).get("hourly")
        hourly_12 = hourly_all[:12] if isinstance(hourly_all, list) else hourly_all

        result_for_model = {
            "location": normalized_location,
            "code": (data or {}).get("code"),
            "updateTime": (data or {}).get("updateTime"),
            "hourly": hourly_12,
            "fxLink": (data or {}).get("fxLink"),
            "refer": (data or {}).get("refer"),
        }

        auth_mode = (
            "jwt"
            if (
                getattr(settings, "qweather_jwt_sub", "")
                and getattr(settings, "qweather_jwt_kid", "")
                and getattr(settings, "qweather_jwt_private_key_path", "")
            )
            else "api_key"
        )
        payload = {
            "tool": "get_weather_12h",
            "ok": True,
            "startedMs": started_ms,
            "finishedMs": int(time.time() * 1000),
            "request": {
                "location": normalized_location,
                "lang": lang,
                "unit": unit,
                "adm": adm,
                "range": range_,
                "number": number,
            },
            "auth": {
                "mode": auth_mode,
                "sub": str(getattr(settings, "qweather_jwt_sub", "") or "").strip() or None,
                "kid": str(getattr(settings, "qweather_jwt_kid", "") or "").strip() or None,
            },
            "geoLookup": chosen_geo,
            "result": result_for_model,
        }
        return NetworkToolResult(content_for_model=json.dumps(result_for_model, ensure_ascii=False), debug_payload=payload)

    @staticmethod
    def _normalize_location(location: str) -> str:
        """规范化 location 参数。

        支持：
        - LocationID (纯数字)
        - "lon,lat" 坐标（十进制，最多四位小数）

        注意：ipinfo 返回的是 "lat,lon"，本服务会在 get_ip_location 中额外给出 lon_lat。
        """

        raw = str(location or "").strip()
        if not raw:
            return raw

        if raw.isdigit():
            return raw

        if "," not in raw:
            return raw

        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 2:
            return raw

        lon, lat = parts
        return NetworkToolsService._normalize_lon_lat(lon=lon, lat=lat)

    @staticmethod
    def _normalize_lon_lat(*, lon: str, lat: str) -> str:
        def _to_float(v: str) -> float:
            return float(str(v).strip())

        lon_f = _to_float(lon)
        lat_f = _to_float(lat)

        # 最多四位小数（设备定位可提供更高精度；公网 IP 定位也不受影响）
        lon_s = f"{lon_f:.4f}".rstrip("0").rstrip(".")
        lat_s = f"{lat_f:.4f}".rstrip("0").rstrip(".")
        return f"{lon_s},{lat_s}"
