from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from backend_py.config import settings


logger = logging.getLogger("backend_py.llm")


class LLMService:
    """DashScope Qwen via OpenAI-compatible API."""

    def __init__(self) -> None:
        if not settings.dashscope_api_key:
            raise RuntimeError("千问API密钥未配置，请设置DASHSCOPE_API_KEY环境变量")

        self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.model_default = "qwen-plus"

        self.system_prompt = (
            "你是一位友好、自然、口语化的电脑语音助手。你的目标是和用户进行顺畅的对话式交流，理解用户的意图并把它转换为具体可执行的电脑操作或直接给出有用的回复。\n\n"
            "输出格式（必须严格遵守）：\n"
            "第一行必须是：INTENT_JSON: {JSON}\n"
            "从第二行开始必须是：SAY: 你要对用户说的话（纯文本，适合TTS朗读）\n\n"
            "INTENT_JSON 的 JSON 结构（必须是单行 JSON，不要换行）：\n"
            "- mode: \"ask\" | \"act\" | \"both\"（提问式 / 操作式 / 提问+操作）\n"
            "- confidence: 0 到 1 之间的小数\n"
            "- actions: 数组，元素形如 {\"name\": \"工具名\", \"arguments\": {}}，没有动作就用 []\n"
            "- reason: 简短原因（可选，10~30字）\n\n"
            "对话风格要求：\n"
            "- 用口语化、简洁、有人情味的语气说话，就像在和用户聊天\n"
            "- 除了第一行的 INTENT_JSON 外，SAY 部分禁止使用任何 Markdown 或装饰符号，不要输出表情\n"
            "- 句子尽量短而清楚，适合语音朗读，避免堆砌标点和冗长段落\n"
            "- 如需要澄清，只提出一个关键问题，不要连续追问\n\n"
            "工具与动作说明：\n"
            "- 如果用户是在\"想听你讲故事/讲笑话/念诗/读文章/解释内容\"，这属于对话（ask），不要误判为播放音乐\n"
            "- 当你判断需要执行操作时：在 INTENT_JSON.actions 里写出动作；必要时也可以同时进行工具调用\n"
            "- 仅可使用以下工具：play_music、stop_music、open_app、write_article、write_file、send_message、write_run_code、file_control\n"
            "- 危险或高风险操作应提示用户确认\n"
            "- stop_music 仅在用户明确要求停止、暂停、关闭音乐时才使用\n\n"
            "回复长度控制：\n"
            "- 默认尽量控制在50字以内\n"
            "- 用户明确要求详细回答、朗读诗歌、讲故事等场景：可以放宽，但仍尽量精炼\n\n"
            "始终以中文为主，保留必要的英文专有名词。注意：SAY 部分必须是纯文本、适合TTS朗读。"
        )

        self.function_definitions: List[Dict[str, Any]] = [
            {
                "name": "play_music",
                "description": "播放音乐",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "enum": ["spotify", "apple", "local"],
                            "description": "音乐来源",
                        },
                        "query": {"type": "string", "description": "搜索的歌曲或艺术家名称（可选）"},
                    },
                },
            },
            {
                "name": "stop_music",
                "description": "停止当前正在播放的音乐。仅当用户明确要求停止音乐、暂停音乐、关闭音乐时才调用此工具。",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "open_app",
                "description": "打开应用程序",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "应用程序名称"}},
                    "required": ["name"],
                },
            },
            {
                "name": "write_article",
                "description": "写文章",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "description": "文章主题"},
                        "style": {
                            "type": "string",
                            "enum": ["formal", "casual", "professional", "creative"],
                            "description": "写作风格",
                        },
                        "length": {
                            "type": "string",
                            "enum": ["short", "medium", "long"],
                            "description": "文章长度",
                        },
                    },
                    "required": ["topic"],
                },
            },
            {
                "name": "write_file",
                "description": "写文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "content": {"type": "string", "description": "文件内容"},
                        "mode": {
                            "type": "string",
                            "enum": ["create", "append", "overwrite"],
                            "description": "写入模式",
                        },
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "send_message",
                "description": "发送消息",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "收件人/目标"},
                        "content": {"type": "string", "description": "消息内容"},
                        "channel": {
                            "type": "string",
                            "enum": ["auto", "sms", "email", "im"],
                            "description": "发送渠道",
                        },
                    },
                    "required": ["target", "content"],
                },
            },
            {
                "name": "write_run_code",
                "description": "编写并运行代码（高风险，通常需要用户确认）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "language": {
                            "type": "string",
                            "enum": ["javascript", "python", "bash"],
                            "description": "代码语言",
                        },
                        "code": {"type": "string", "description": "代码内容"},
                        "run": {"type": "boolean", "description": "是否运行代码"},
                    },
                    "required": ["language", "code"],
                },
            },
            {
                "name": "file_control",
                "description": "文件管理（列出/读取/移动/删除等，高风险操作需确认）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": ["list", "read", "move", "copy", "delete", "mkdir"],
                            "description": "文件操作类型",
                        },
                        "path": {"type": "string", "description": "目标路径"},
                        "destination": {"type": "string", "description": "目标路径（move/copy时）"},
                    },
                    "required": ["operation", "path"],
                },
            },
        ]

    async def invoke_llm(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 512,
    ) -> Dict[str, Any]:
        used_model = model or self.model_default
        tool_defs = tools or self.function_definitions

        payload = {
            "model": used_model,
            "messages": [{"role": "system", "content": self.system_prompt}, *messages],
            "temperature": 0.7,
            "max_tokens": max_tokens,
            "tools": [{"type": "function", "function": t} for t in tool_defs],
            "tool_choice": "auto",
        }

        headers = {
            "Authorization": f"Bearer {settings.dashscope_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(base_url=self.base_url, timeout=60.0) as client:
            resp = await client.post("/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}

        return {
            "text": msg.get("content") or "",
            "toolCalls": msg.get("tool_calls") or [],
            "model": used_model,
            "usage": data.get("usage"),
        }

    @staticmethod
    def extract_say_text(raw_text: str) -> str:
        text = str(raw_text or "")
        lines = text.splitlines()
        if not lines:
            return ""
        first = lines[0]
        if first.startswith("INTENT_JSON:"):
            rest = lines[1:]
            if rest and rest[0].startswith("SAY:"):
                rest[0] = rest[0][len("SAY:") :].lstrip()
            return "\n".join(rest).strip()
        if text.startswith("SAY:"):
            return text[len("SAY:") :].strip()
        return text.strip()
