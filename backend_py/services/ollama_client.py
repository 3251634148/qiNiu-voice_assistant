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
        self.timeout_sec = float(timeout_sec)

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

            normalized.append(
                {
                    "id": str(call.get("id") or f"ollama_tool_{now}_{idx}"),
                    "type": "function",
                    "function": {
                        "name": name.strip(),
                        "arguments": args_json,
                    },
                }
            )
        return normalized

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
        """调用 Ollama /api/chat。

        Args:
            model: 模型名（例如 qwen3-vl:4b）
            messages: OpenAI 风格 messages（role/content）
            images_base64: 可选，附加到最后一条 user message 的 images（base64）
        """

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

        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            resp = await client.post(url, json=payload)

        if resp.status_code != 200:
            raise RuntimeError(f"Ollama /api/chat 请求失败: {resp.status_code} {resp.text[:400]}")

        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"Ollama /api/chat 返回非 JSON: {resp.text[:400]}")

        msg = data.get("message") if isinstance(data, dict) else None
        msg = msg if isinstance(msg, dict) else {}

        # 兼容不同版本字段：优先 message.content。
        content = self._stringify_message_content(msg.get("content"))

        if not content.strip():
            # 兜底：部分实现可能直接返回 response。
            try:
                content = self._stringify_message_content(data.get("response"))
            except Exception:
                content = ""

        return OllamaChatResult(
            content=str(content or ""),
            tool_calls=self._normalize_tool_calls(msg.get("tool_calls")),
            model=str(data.get("model") or model),
            done=bool(data.get("done")) if isinstance(data, dict) else False,
            done_reason=str(data.get("done_reason") or "") or None if isinstance(data, dict) else None,
            raw=dict(data) if isinstance(data, dict) else {"raw": data},
        )

    @staticmethod
    def pretty_json(obj: Any) -> str:
        try:
            return json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception:
            return str(obj)
