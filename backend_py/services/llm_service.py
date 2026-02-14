from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from backend_py.config import settings
from backend_py.services.network_tools_service import NetworkToolsService


logger = logging.getLogger("backend_py.llm")


class LLMService:
    """千问（DashScope）OpenAI 兼容模式调用封装。"""

    def __init__(self) -> None:
        self.stub_enabled = str(os.getenv("VOICE_ASSISTANT_LLM_STUB", "")).strip().lower() in {"1", "true", "yes"}

        if not self.stub_enabled and not settings.dashscope_api_key:
            raise RuntimeError("千问API密钥未配置，请设置DASHSCOPE_API_KEY环境变量")

        self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.model_default = "qwen-plus"
        self.network_tools = NetworkToolsService()

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
            "- 默认不要追问：用户让你做一件事时，优先直接完成并给出结果\n"
            "- 只有当缺少关键且无法合理默认的信息时，才允许提出 1 个问题澄清；禁止连续追问\n"
            "- 当用户回复‘随便/都行/你决定’时，视为授权：必须直接产出最终结果，不要再提问\n\n"
            "工具与动作说明：\n"
            "- 如果用户是在\"想听你讲故事/讲笑话/念诗/读文章/解释内容\"，这属于对话（ask），不要误判为播放音乐\n"
            "- 当你判断需要执行操作时：在 INTENT_JSON.actions 里写出动作\n"
            "- 如果 SAY 里出现\"正在播放/已开始播放/我给你放\"等承诺，INTENT_JSON.actions 必须包含可执行的播放动作；否则不要声称已播放\n"
            "- 用户要求播放音乐但未指定播放器时，默认使用 play_music(source=\"kugou\")\n"
            "- 当用户请求播放具体歌曲（例如‘帮我播放周杰伦的告白气球’）时：必须使用 music_ui(player=\"kugou\", action=\"search\", query=...)\n"
            "  - query 必须是规范化搜索词：去掉礼貌/指令词（如‘帮我/请/麻烦/给我/播放/放/来一首/我想听/我要听’等），并将‘歌手的歌名/歌手-歌名/歌手 歌名’等统一为‘歌手 歌名’（用空格分隔）\n"
            "  - 禁止把‘帮我播放/请播放/麻烦’之类词语放进 query\n"
            "- 当用户说‘随便/随机/来点音乐/适合我现在心情/按我现在的心情’等泛化请求时：\n"
            "  - 你必须自己挑选 1 首真实存在的歌曲，并使用 music_ui(player=\"kugou\", action=\"search\", query=...)\n"
            "  - query 必须是‘歌手 歌名’（用空格分隔），且不得是‘随机/随便/来点音乐/心情’等泛词\n"
            "  - 禁止在此场景使用 favorites_first（除非用户明确说‘播放我喜欢/喜欢的歌/收藏’）\n"
            "- 当用户要求播放‘我喜欢/喜欢的歌/收藏/我收藏的歌/我常听的歌’时，才允许使用 music_ui(player=\"kugou\", action=\"favorites_first\")\n"
            "  - 如果用户说‘第一首’，可加 pickMode=\"first\"\n"
            "  - 如果用户说‘随机/随便’，可加 pickMode=\"random\"\n"
            "- 写作/创作类纯文本任务（写诗、写文案、写段子、总结、解释等）：默认直接产出，不要反复确认。\n"
            "  - 若用户没给风格/字数等细节：你必须自行采用合理默认值并直接输出成品\n"
            "  - 若用户说‘随便/都行/你决定’：你必须一步到位输出最终成品（不要只说‘我来写’）\n"
            "- 支持多步任务：当一个目标需要多个步骤（例如先打开应用再发送消息），优先使用 execute_workflow，一次性给出 steps\n"
            "- 联网信息能力（若工具可用）：当你需要获取实时信息（当前时间、天气、最新新闻、刚发生的事件、互联网搜索结果）时，优先调用对应工具；不要编造\n"
            "  - web_search：互联网搜索，返回带来源链接的摘要\n"
            "  - get_latest_news：获取近期新闻列表（带来源链接）\n"
            "  - get_ip_location：获取当前公网 IP 的城市/经纬度/时区（用于本地化天气；精度有限，可能存在城市级偏差）\n"
            "  - get_weather_now：获取指定 location 的当前天气（location 可为城市/区县/LocationID/经纬度；必要时会先解析 LocationID）\n"
            "  - get_weather_12h：获取未来 12 小时天气预报（用于判断未来是否降雨、转凉等趋势，并给出温度区间）\n"
            "  - get_current_time：获取本地当前时间\n"
            "  - 天气问题优先策略：\n"
            "    1) 若系统已提供‘设备定位 lon_lat’（用户已授权）：直接用该 lon_lat 调用 get_weather_now + get_weather_12h，不要调用 get_ip_location\n"
            "    2) 用户问‘我这里/当前位置/现在所在位置’且没有设备定位：先 get_ip_location，再用返回的 lon_lat 调用 get_weather_now + get_weather_12h\n"
            "    3) 用户问指定城市：直接 get_weather_now(location=城市名) + get_weather_12h(location=城市名)。如遇重名可加 adm/range=cn\n"
            "  - 天气回答必须包含：当前天气（温度/湿度/风/天气描述）+ 未来12小时温度区间（最低~最高）+ 未来趋势（例如傍晚可能下雨/转凉）+ 暖心建议（带伞/加衣等）\n"
            "  - 失败强兜底（必须执行）：当任一联网工具返回 error，或 get_weather_now/get_weather_12h 返回 code!=200 时，必须立刻调用 web_search 用互联网结果直接回答；禁止再问用户是否要搜索；不要说接口不可用\n"
            "- 仅可使用以下工具：play_music、music_ui、media_control、stop_music、open_app、write_article、write_file、write_run_code、file_control、run_tests、execute_workflow、web_search、get_latest_news、get_ip_location、get_weather_now、get_weather_12h、get_current_time\n"
            "- music_ui 用于通过 UI 自动化控制音乐播放器（例如酷狗/Apple Music 的搜索播放、我喜欢列表播放等）。这是高风险操作，通常需要用户确认\n"
            "- media_control 用于系统媒体键兜底（播放/暂停、上一首、下一首、音量、当前曲目信息等），通常不需要确认\n"
            "- send_message（企业微信/飞书/微信等 API）当前不启用，因为密钥信息难以获得\n"
            "- run_tests 用于在指定工作目录运行单测命令（高风险，通常需要用户确认）\n"
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
                "description": "播放音乐（低风险）。支持 kugou（打开应用+媒体键）、apple、spotify；若提供 query，可用于按歌单名播放（Apple Music）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "enum": ["spotify", "apple", "kugou", "local"],
                            "description": "音乐来源",
                        },
                        "query": {"type": "string", "description": "歌曲/艺术家/歌单名称（可选）"},
                    },
                },
            },
            {
                "name": "music_ui",
                "description": "通过 UI 自动化控制音乐播放器（高风险，需要确认）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "player": {
                            "type": "string",
                            "enum": ["kugou", "apple_music"],
                            "description": "播放器类型",
                        },
                        "action": {
                            "type": "string",
                            "enum": ["favorites_first", "playlist", "search"],
                            "description": "操作类型：我喜欢播放/指定歌单/搜索播放",
                        },
                        "query": {"type": "string", "description": "歌单名或搜索关键词（playlist/search 时需要）"},
                        "pickMode": {
                            "type": "string",
                            "enum": ["first", "random"],
                            "description": "在我喜欢列表中选歌方式（favorites_first 可选）：first=第一首，random=随机一首",
                        },
                        "debug": {"type": "boolean", "description": "是否返回调试信息（可选）"},
                        "dryRun": {"type": "boolean", "description": "只演练不点击（可选）"},
                    },
                    "required": ["player", "action"],
                },
            },
            {
                "name": "media_control",
                "description": "系统媒体控制兜底（媒体键/音量/当前曲目信息）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "play_pause",
                                "next",
                                "previous",
                                "volume_up",
                                "volume_down",
                                "mute",
                                "get_now_playing",
                                "set_volume_delta"
                            ],
                            "description": "媒体控制动作",
                        },
                        "delta": {"type": "number", "description": "音量变化（仅 set_volume_delta 需要，整数）"},
                    },
                    "required": ["action"],
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
            {
                "name": "run_tests",
                "description": "在指定目录运行单测命令（高风险，通常需要用户确认）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cwd": {"type": "string", "description": "工作目录（必须是允许目录内的绝对路径或相对 home 的路径）"},
                        "command": {"type": "string", "description": "要执行的测试命令，例如：npm test / pytest"},
                        "timeoutSec": {"type": "number", "description": "超时时间（秒，可选）"},
                    },
                    "required": ["cwd", "command"],
                },
            },
            {
                "name": "execute_workflow",
                "description": "执行多步工作流（用于一个目标需要多个动作的场景，例如先打开应用再发送消息）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "description": "步骤工具名"},
                                    "arguments": {"type": "object", "description": "步骤参数"},
                                },
                                "required": ["name"],
                            },
                            "description": "步骤列表，按顺序执行",
                        }
                    },
                    "required": ["steps"],
                },
            },
        ]

    def _invoke_llm_stub(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """A deterministic LLM stub for local E2E tests.

        Enabled by env var `VOICE_ASSISTANT_LLM_STUB=1`.
        """

        last = (messages[-1].get("content") if messages else "") or ""
        user_text = str(last)

        def _tool_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
            now = int(time.time() * 1000)
            return {
                "id": f"stub_{name}_{now}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            }

        tool_calls: list[dict[str, Any]] = []
        say = "好的。"
        intent_actions: list[dict[str, Any]] = []

        if "我喜欢" in user_text or "喜欢的歌" in user_text or "我喜爱" in user_text:
            pick_mode = "first"
            if "随机" in user_text or "随便" in user_text:
                pick_mode = "random"
                say = "我可以在酷狗打开我喜欢并随机播放一首，这需要你确认一下。"
            elif "第一首" in user_text or "第一首歌" in user_text:
                pick_mode = "first"
                say = "我可以在酷狗打开我喜欢并播放第一首，这需要你确认一下。"
            else:
                say = "我可以在酷狗打开我喜欢并播放一首歌，这需要你确认一下。"

            args = {"player": "kugou", "action": "favorites_first", "pickMode": pick_mode, "debug": True}
            tool_calls = [_tool_call("music_ui", args)]
            intent_actions = [{"name": "music_ui", "arguments": args}]

        elif any(k in user_text for k in ["播放", "帮我播放", "给我放", "我想听", "我要听", "来一首", "来首", "放一首", "放", "听"]):
            # 尽力而为的 query 归一化（仅用于 stub 的 E2E 稳定性）。
            q = user_text
            for prefix in [
                "帮我播放",
                "请播放",
                "麻烦播放",
                "给我播放",
                "我想听",
                "我要听",
                "帮我放",
                "给我放",
                "播放",
                "来一首",
                "来首",
                "放一首",
                "放",
                "听",
            ]:
                q = q.replace(prefix, " ")
            q = q.replace("的", " ")
            q = " ".join(q.split())

            # 对泛化/随机请求的确定性 stub 兜底（避免测试不稳定）。
            if q in {"随机", "随便", "来点音乐", "来点歌", "听歌", "听音乐"}:
                q = "周杰伦 告白气球"

            if q:
                say = f"我可以用酷狗搜索并播放“{q}”。这需要你确认一下。"
                args = {"player": "kugou", "action": "search", "query": q, "debug": True}
                tool_calls = [_tool_call("music_ui", args)]
                intent_actions = [{"name": "music_ui", "arguments": args}]

        intent_obj = {"mode": "both", "confidence": 0.8, "actions": intent_actions, "reason": "stub"}
        text = f"INTENT_JSON: {json.dumps(intent_obj, ensure_ascii=False)}\nSAY: {say}"

        return {"text": text, "toolCalls": tool_calls, "model": "stub", "usage": None}

    def build_tool_definitions(self, *, include_network: bool) -> List[Dict[str, Any]]:
        if include_network:
            return [*self.function_definitions, *self.network_tools.get_tool_definitions()]
        return list(self.function_definitions)

    async def invoke_llm(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 512,
        parallel_tool_calls: bool = False,
    ) -> Dict[str, Any]:
        if self.stub_enabled:
            return self._invoke_llm_stub(messages)

        used_model = model or self.model_default
        tool_defs = tools or self.function_definitions

        payload: Dict[str, Any] = {
            "model": used_model,
            "messages": [{"role": "system", "content": self.system_prompt}, *messages],
            "temperature": 0.7,
            "max_tokens": max_tokens,
            "tools": [{"type": "function", "function": t} for t in tool_defs],
            "tool_choice": "auto",
        }
        if parallel_tool_calls:
            payload["parallel_tool_calls"] = True

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
