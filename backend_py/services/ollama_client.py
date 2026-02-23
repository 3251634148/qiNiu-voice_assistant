from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import httpx


@dataclass(frozen=True)
class OllamaChatResult:
    """Ollama /api/chat 返回结果（简化版）。"""

    content: str
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
        self.base_url = str(base_url or "").rstrip("/")
        self.timeout_sec = float(timeout_sec)

    async def chat(
        self,
        *,
        model: str,
        messages: Sequence[Dict[str, Any]],
        images_base64: Optional[Sequence[str]] = None,
        stream: bool = False,
        keep_alive: str = "10m",
        options: Optional[Dict[str, Any]] = None,
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

        url = f"{self.base_url}/api/chat"

        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            resp = await client.post(url, json=payload)

        if resp.status_code != 200:
            raise RuntimeError(f"Ollama /api/chat 请求失败: {resp.status_code} {resp.text[:400]}")

        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"Ollama /api/chat 返回非 JSON: {resp.text[:400]}")

        # 兼容不同版本字段：优先 message.content。
        content = ""
        try:
            msg = data.get("message") if isinstance(data, dict) else None
            content = str((msg or {}).get("content") or "")
        except Exception:
            content = ""

        if not content.strip():
            # 兜底：部分实现可能直接返回 response。
            try:
                content = str(data.get("response") or "")
            except Exception:
                content = ""

        return OllamaChatResult(content=str(content or ""), raw=dict(data) if isinstance(data, dict) else {"raw": data})

    @staticmethod
    def pretty_json(obj: Any) -> str:
        try:
            return json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception:
            return str(obj)
