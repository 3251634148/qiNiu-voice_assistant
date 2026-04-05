from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import httpx


@dataclass(frozen=True)
class OllamaChatResult:
    """Ollama /api/chat 返回结果（统一为主会话可复用的结构）。"""

    content: str
    tool_calls: List[Dict[str, Any]]
    model: str
    done: bool
    done_reason: Optional[str]
    raw: Dict[str, Any]
    thinking: str = ""
    stream: bool = False
    first_chunk_ms: Optional[int] = None
    duration_ms: Optional[int] = None


class OllamaClient:
    """本地 Ollama REST API 客户端。

    说明：
    - 推理完全在本机进行（http://127.0.0.1:11434）。
    - 图片通过 base64 编码传入 `messages[].images`。
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout_sec: float,
    ) -> None:
        self.base_url = self._normalize_root_base_url(base_url)
        self.timeout_sec = max(5.0, float(timeout_sec))

    @staticmethod
    def _normalize_root_base_url(base_url: str) -> str:
        """将任意 Ollama 入口归一化为根地址。

        兼容以下输入：
        - http://127.0.0.1:11434
        - http://127.0.0.1:11434/
        - http://127.0.0.1:11434/v1
        - http://127.0.0.1:11434/api
        - http://127.0.0.1:11434/api/chat
        """

        normalized = str(base_url or "").strip().rstrip("/")
        if not normalized:
            return "http://127.0.0.1:11434"

        for suffix in ("/api/chat", "/api", "/v1/chat/completions", "/v1"):
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)].rstrip("/")
                break
        return normalized or "http://127.0.0.1:11434"

    @staticmethod
    def _stringify_message_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, (dict, list)):
            return json.dumps(content, ensure_ascii=False)
        if content is None:
            return ""
        return str(content)

    @staticmethod
    def _normalize_tool_calls(raw_tool_calls: Any) -> List[Dict[str, Any]]:
        """把 Ollama 原生 tool_calls 规范成项目内部统一结构。

        说明：
        - Ollama 常把 `function.arguments` 直接返回为对象。
        - 现有 `ConversationController` / `ToolRouter` 默认读取 JSON 字符串。
        - 因此这里统一转换为 `function.arguments=<json string>`。
        """

        if not isinstance(raw_tool_calls, list):
            return []

        now = int(time.time() * 1000)
        normalized: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for idx, call in enumerate(raw_tool_calls):
            if not isinstance(call, dict):
                continue
            fn = call.get("function") if isinstance(call.get("function"), dict) else {}
            name = fn.get("name") or call.get("name")
            if not isinstance(name, str) or not name.strip():
                continue

            args_raw = fn.get("arguments")
            if isinstance(args_raw, str):
                args_json = args_raw
            elif isinstance(args_raw, (dict, list)):
                args_json = json.dumps(args_raw, ensure_ascii=False)
            elif args_raw is None:
                args_json = "{}"
            else:
                args_json = json.dumps(args_raw, ensure_ascii=False)

            normalized_call = {
                "id": str(call.get("id") or f"ollama_tool_{now}_{idx}"),
                "type": "function",
                "function": {
                    "name": name.strip(),
                    "arguments": args_json,
                },
            }
            dedupe_key = json.dumps(normalized_call, ensure_ascii=False, sort_keys=True)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized.append(normalized_call)
        return normalized

    def _build_http_timeout(self) -> httpx.Timeout:
        total = float(self.timeout_sec)
        connect = min(10.0, total)
        return httpx.Timeout(connect=connect, read=total, write=total, pool=total)

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    def _build_result_from_data(
        self,
        data: Dict[str, Any],
        *,
        model: str,
        stream: bool,
        thinking: str = "",
        tool_calls_raw: Optional[List[Dict[str, Any]]] = None,
        first_chunk_ms: Optional[int] = None,
        duration_ms: Optional[int] = None,
    ) -> OllamaChatResult:
        msg = data.get("message") if isinstance(data, dict) else None
        msg = msg if isinstance(msg, dict) else {}

        content = self._stringify_message_content(msg.get("content"))
        if not content.strip():
            content = self._stringify_message_content(data.get("response"))

        thinking_text = str(thinking or self._stringify_message_content(msg.get("thinking")) or "")
        normalized_tool_calls = self._normalize_tool_calls(tool_calls_raw if tool_calls_raw is not None else msg.get("tool_calls"))

        return OllamaChatResult(
            content=str(content or ""),
            thinking=thinking_text,
            tool_calls=normalized_tool_calls,
            model=str(data.get("model") or model),
            done=bool(data.get("done")) if isinstance(data, dict) else False,
            done_reason=str(data.get("done_reason") or "") or None if isinstance(data, dict) else None,
            raw=dict(data) if isinstance(data, dict) else {"raw": data},
            stream=bool(stream),
            first_chunk_ms=first_chunk_ms,
            duration_ms=duration_ms,
        )

    async def _chat_non_streaming(self, *, url: str, payload: Dict[str, Any], model: str) -> OllamaChatResult:
        started_ms = self._now_ms()
        async with httpx.AsyncClient(timeout=self._build_http_timeout()) as client:
            resp = await client.post(url, json=payload)

        if resp.status_code != 200:
            raise RuntimeError(f"Ollama /api/chat 请求失败: {resp.status_code} {resp.text[:400]}")

        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"Ollama /api/chat 返回非 JSON: {resp.text[:400]}")

        if not isinstance(data, dict):
            raise RuntimeError(f"Ollama /api/chat 返回结构异常: {str(data)[:400]}")

        duration_ms = self._now_ms() - started_ms
        data.setdefault(
            "streamMeta",
            {
                "mode": "non_stream",
                "durationMs": duration_ms,
                "timeoutSec": self.timeout_sec,
            },
        )
        return self._build_result_from_data(
            data,
            model=model,
            stream=False,
            duration_ms=duration_ms,
        )

    async def _chat_streaming(self, *, url: str, payload: Dict[str, Any], model: str) -> OllamaChatResult:
        started_ms = self._now_ms()
        first_chunk_ms: Optional[int] = None
        content_parts: List[str] = []
        thinking_parts: List[str] = []
        raw_tool_calls: List[Dict[str, Any]] = []
        final_data: Dict[str, Any] = {}
        model_name = str(model or "")

        async with httpx.AsyncClient(timeout=self._build_http_timeout()) as client:
            async with client.stream("POST", url, json=payload) as resp:
                if resp.status_code != 200:
                    text = (await resp.aread()).decode("utf-8", errors="replace")
                    raise RuntimeError(f"Ollama /api/chat 请求失败: {resp.status_code} {text[:400]}")

                async for line in resp.aiter_lines():
                    raw_line = str(line or "").strip()
                    if not raw_line:
                        continue

                    try:
                        chunk = json.loads(raw_line)
                    except Exception as e:
                        raise RuntimeError(f"Ollama /api/chat 流式分块解析失败: {e}; line={raw_line[:200]}")

                    if not isinstance(chunk, dict):
                        continue

                    if first_chunk_ms is None:
                        first_chunk_ms = self._now_ms() - started_ms

                    final_data = dict(chunk)
                    if chunk.get("model"):
                        model_name = str(chunk.get("model") or model_name)

                    msg = chunk.get("message") if isinstance(chunk.get("message"), dict) else {}
                    thinking_chunk = self._stringify_message_content(msg.get("thinking"))
                    if thinking_chunk:
                        thinking_parts.append(thinking_chunk)

                    content_chunk = self._stringify_message_content(msg.get("content"))
                    if content_chunk:
                        content_parts.append(content_chunk)

                    tc = msg.get("tool_calls")
                    if isinstance(tc, list) and tc:
                        raw_tool_calls.extend(tc)

        if not final_data:
            raise RuntimeError("Ollama /api/chat 流式响应为空")

        duration_ms = self._now_ms() - started_ms
        final_data["message"] = {
            **(final_data.get("message") if isinstance(final_data.get("message"), dict) else {}),
            "content": "".join(content_parts),
            "thinking": "".join(thinking_parts),
            "tool_calls": raw_tool_calls,
        }
        final_data["streamMeta"] = {
            "mode": "stream",
            "timeoutSec": self.timeout_sec,
            "firstChunkMs": first_chunk_ms,
            "durationMs": duration_ms,
            "contentChars": len("".join(content_parts)),
            "thinkingChars": len("".join(thinking_parts)),
            "toolCallsCount": len(raw_tool_calls),
        }

        return self._build_result_from_data(
            final_data,
            model=model_name or model,
            stream=True,
            thinking="".join(thinking_parts),
            tool_calls_raw=raw_tool_calls,
            first_chunk_ms=first_chunk_ms,
            duration_ms=duration_ms,
        )

    async def chat(
        self,
        *,
        model: str,
        messages: Sequence[Dict[str, Any]],
        images_base64: Optional[Sequence[str]] = None,
        stream: bool = False,
        keep_alive: str = "10m",
        options: Optional[Dict[str, Any]] = None,
        tools: Optional[Sequence[Dict[str, Any]]] = None,
        response_format: Optional[Any] = None,
    ) -> OllamaChatResult:
        """调用 Ollama /api/chat。"""

        if not str(model or "").strip():
            raise RuntimeError("Ollama model 不能为空")

        msg_list: List[Dict[str, Any]] = [dict(m) for m in list(messages or [])]
        if not msg_list:
            raise RuntimeError("messages 不能为空")

        if images_base64:
            # Ollama 的 images 字段挂在单条 message 上。
            last = dict(msg_list[-1])
            last.setdefault("role", "user")
            last["images"] = [str(x) for x in images_base64 if str(x)]
            msg_list[-1] = last

        payload: Dict[str, Any] = {
            "model": str(model),
            "messages": msg_list,
            "stream": bool(stream),
            "keep_alive": str(keep_alive or "10m"),
        }
        if options:
            payload["options"] = dict(options)
        if tools:
            payload["tools"] = [dict(t) for t in tools]
        if response_format is not None:
            payload["format"] = response_format

        url = f"{self.base_url}/api/chat"
        if bool(stream):
            return await self._chat_streaming(url=url, payload=payload, model=str(model))
        return await self._chat_non_streaming(url=url, payload=payload, model=str(model))

    @staticmethod
    def pretty_json(obj: Any) -> str:
        try:
            return json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception:
            return str(obj)
