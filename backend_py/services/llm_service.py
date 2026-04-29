from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from backend_py.config import settings
from backend_py.services.network_tools_service import NetworkToolsService
from backend_py.services.ollama_client import OllamaClient


logger = logging.getLogger("backend_py.llm")


class LLMService:
    """LLM 调用封装，支持 DashScope（远程）和 Ollama（本地）两种后端。

    通过环境变量 LLM_PROVIDER 切换：
    - dashscope（默认）：阿里云千问 OpenAI 兼容模式
    - ollama：本地 Ollama（原生 `/api/chat`）
    """

    def __init__(self) -> None:
        self.stub_enabled = str(os.getenv("VOICE_ASSISTANT_LLM_STUB", "")).strip().lower() in {"1", "true", "yes"}
        self.provider = settings.llm_provider  # "dashscope" or "ollama"
        self.ollama_client: Optional[OllamaClient] = None

        if self.provider == "ollama":
            self.base_url = settings.ollama_base_url
            self.api_key = "ollama"  # Ollama 不需要真实 key，但 HTTP 头需要非空值
            self.model_default = settings.ollama_model
            self.ollama_timeout_sec = float(getattr(settings, "ollama_timeout_sec", 300.0) or 300.0)
            self.ollama_client = OllamaClient(base_url=self.base_url, timeout_sec=self.ollama_timeout_sec)
            logger.info(
                "LLM 后端: Ollama (base_url=%s, model=%s, timeoutSec=%.1f)",
                self.base_url,
                self.model_default,
                self.ollama_timeout_sec,
            )
        else:
            if not self.stub_enabled and not settings.dashscope_api_key:
                raise RuntimeError("千问API密钥未配置，请设置DASHSCOPE_API_KEY环境变量")
            self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            self.api_key = settings.dashscope_api_key
            self.model_default = "qwen-plus"
            logger.info("LLM 后端: DashScope (model=%s)", self.model_default)
        self.network_tools = NetworkToolsService()

        # 本地设备定位工具（macOS CoreLocation），由会话开关决定是否对模型暴露。
        self.device_location_tool_def = {
            "name": "get_device_location",
            "description": "获取本机设备的实时位置（macOS CoreLocation）。返回经纬度、精度（米）与街道/区/市等地址信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "timeoutSec": {"type": "number", "description": "可选：超时时间（秒），默认 12"}
                },
            },
        }

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
            "  - get_device_location：获取本机设备的实时位置（macOS CoreLocation），精度更高。\n"
            "  - get_ip_location：获取当前公网 IP 的城市/经纬度/时区（精度有限，不应用于当前位置/未指明城市的天气）\n"
            "  - get_weather_now：获取指定 location 的当前天气（location 可为城市/区县/LocationID/经纬度；必要时会先解析 LocationID）\n"
            "  - get_weather_12h：获取未来 12 小时天气预报（用于判断未来是否降雨、转凉等趋势，并给出温度区间）\n"
            "  - get_current_time：获取本地当前时间\n"
            "  - 天气/定位问题优先策略（必须遵守）：\n"
            "    1) 用户问‘我在哪/当前位置/我现在所处位置’：必须调用 get_device_location 获取经纬度+地址信息，再总结回答；禁止调用 get_ip_location\n"
            "    2) 用户问天气但未指明城市（例如‘今天天气怎么样？’）：必须先调用 get_device_location，然后用返回的 lon_lat 调用 get_weather_now + get_weather_12h；禁止调用 get_ip_location\n"
            "    3) 用户明确问某个城市：直接 get_weather_now(location=城市名) + get_weather_12h(location=城市名)。如遇重名可加 adm/range=cn\n"
            "    4) 若无法调用 get_device_location（工具不可用/未授权）：只允许提示用户到设置开启‘设备定位’并授予系统定位权限，不要回退到 IP 定位\n"
            "  - 天气回答必须包含：当前天气（温度/湿度/风/天气描述）+ 未来12小时温度区间（最低~最高）+ 未来趋势（例如傍晚可能下雨/转凉）+ 暖心建议（带伞/加衣等）\n"
            "  - 失败强兜底（必须执行）：当【天气相关联网工具】返回 error，或 get_weather_now/get_weather_12h 返回 code!=200 时，必须立刻调用 web_search 用互联网结果直接回答；禁止再问用户是否要搜索；不要说接口不可用。\n"
            "    - 例外：若 get_device_location 失败/未授权，只允许提示用户到设置开启‘设备定位’并授予系统定位权限，不要调用 web_search，也不要回退 IP。\n"
            "- 仅可使用以下工具：play_music、music_ui、douyin_ui、wecom_ui、media_control、stop_music、open_app、write_article、write_file、write_run_code、file_control、run_tests、execute_workflow、web_search、get_latest_news、get_device_location、get_ip_location、get_weather_now、get_weather_12h、get_current_time\n"
            "- music_ui 用于通过 UI 自动化控制音乐播放器（例如酷狗/Apple Music 的搜索播放、我喜欢列表播放等）。这是高风险操作，通常需要用户确认\n"
            "- douyin_ui 用于通过 UI 自动化控制抖音：搜索并播放最匹配视频。高风险操作，通常需要用户确认\n"
            "  - query 必须是干净的搜索词：去掉‘帮我/请/麻烦/在抖音/抖音里/给我/搜索/播放/找一下’等指令词，只保留要搜索的主题\n"
            "- wecom_ui 用于通过 UI 自动化控制企业微信：搜索联系人并发送消息。高风险操作，通常需要用户确认\n"
            "  - contactName 必须是联系人姓名或备注（不要带‘给/发消息给/@’等前缀）\n"
            "  - message 必须是要发送的原始消息正文（不要加入多余解释）\n"
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
        self.ollama_compact_system_prompt = (
            "你是本地运行的电脑语音助手。你必须只输出符合 response schema 的 JSON，不要输出 Markdown，不要输出 schema 外字段。\n"
            "返回 JSON 时必须填写这些字段：mode、confidence、say、actions，可选 reason。\n"
            "目标：用最短路径判断用户意图，给出适合 TTS 朗读的 say，并在 actions 中写出可执行动作。\n"
            "关键规则：\n"
            "- say 必须是中文纯文本，简洁自然，默认 30 字内。\n"
            "- 没有真正要执行的动作时，actions 必须是 []。\n"
            "- 不要声称已经执行了尚未执行的操作。\n"
            "- 播放具体歌曲时，必须使用 music_ui(player=\"kugou\", action=\"search\", query=\"歌手 歌名\")。\n"
            "- 播放泛化音乐时，可使用 play_music(source=\"kugou\")。\n"
            "- 只有用户明确提到我喜欢/收藏/常听的歌时，才允许使用 favorites_first。\n"
            "- 写作、解释、闲聊、问答等纯文本任务，actions 置空。\n"
            "- 天气、新闻、时间、联网搜索等实时信息优先使用对应工具；设备定位与天气要遵守现有定位策略。\n"
            "- 缺少关键参数时最多问 1 个问题，否则直接给结果。"
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
                "name": "douyin_ui",
                "description": "通过 UI 自动化控制抖音：搜索并播放最匹配视频（高风险，需要确认）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词（必须是干净的搜索词）"},
                        "debug": {"type": "boolean", "description": "是否返回调试信息（可选）"},
                        "dryRun": {"type": "boolean", "description": "只演练不点击（可选）"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "wecom_ui",
                "description": "通过 UI 自动化控制企业微信：搜索联系人并发送消息（高风险，需要确认）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "contactName": {"type": "string", "description": "联系人姓名/备注（不要带前缀）"},
                        "message": {"type": "string", "description": "要发送的消息正文"},
                        "debug": {"type": "boolean", "description": "是否返回调试信息（可选）"},
                        "dryRun": {"type": "boolean", "description": "只演练不点击（可选）"},
                    },
                    "required": ["contactName", "message"],
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

        def _strip_phrases(text: str, phrases: list[str]) -> str:
            v = str(text or "")
            for p in phrases:
                v = v.replace(p, " ")
            return " ".join(v.split())

        if any(k in user_text for k in ["企业微信", "企微", "WeCom"]) and any(k in user_text for k in ["发消息", "发送", "发个", "发", "说"]):
            cleaned = _strip_phrases(
                user_text,
                [
                    "企业微信",
                    "企微",
                    "WeCom",
                    "用",
                    "请",
                    "帮我",
                    "麻烦",
                    "一下",
                ],
            )

            contact = ""
            message = ""

            if "给" in cleaned:
                after_give = cleaned.split("给", 1)[1]
                send_idx = after_give.find("发送")
                if send_idx < 0:
                    send_idx = after_give.find("发")
                if send_idx < 0:
                    send_idx = after_give.find("说")
                if send_idx >= 0:
                    contact = after_give[:send_idx].strip()

            if ":" in cleaned or "：" in cleaned:
                message = cleaned.split(":")[-1].split("：")[-1].strip()
            elif "说" in cleaned:
                message = cleaned.split("说", 1)[1].strip()
            else:
                # 尝试从“发送/发”之后截取消息
                if "发送" in cleaned:
                    message = cleaned.split("发送", 1)[1].strip()
                elif "发" in cleaned:
                    message = cleaned.split("发", 1)[1].strip()

            message = message.replace("消息", " ").replace("信息", " ").strip()

            if contact and message:
                say = f"我可以在企业微信搜索联系人并发送消息给“{contact}”。这需要你确认一下。"
                args = {"contactName": contact, "message": message, "debug": True}
                tool_calls = [_tool_call("wecom_ui", args)]
                intent_actions = [{"name": "wecom_ui", "arguments": args}]

        elif any(k in user_text for k in ["抖音", "Douyin"]) and any(k in user_text for k in ["搜", "搜索", "找", "播放"]):
            q = _strip_phrases(
                user_text,
                [
                    "抖音",
                    "Douyin",
                    "在",
                    "里",
                    "用",
                    "请",
                    "帮我",
                    "麻烦",
                    "一下",
                    "搜索",
                    "搜",
                    "找",
                    "播放",
                ],
            )
            if q:
                say = f"我可以在抖音搜索并播放“{q}”。这需要你确认一下。"
                args = {"query": q, "debug": True}
                tool_calls = [_tool_call("douyin_ui", args)]
                intent_actions = [{"name": "douyin_ui", "arguments": args}]

        elif "我喜欢" in user_text or "喜欢的歌" in user_text or "我喜爱" in user_text:
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

    def build_tool_definitions(self, *, include_network: bool, include_device_location: bool) -> List[Dict[str, Any]]:
        base = list(self.function_definitions)
        if include_device_location:
            base.append(self.device_location_tool_def)

        if include_network:
            return [*base, *self.network_tools.get_tool_definitions()]
        return base

    def _build_system_content(self, *, memory_context: str, output_mode: str) -> str:
        """构建 system prompt。"""

        use_compact_prompt = self.provider == "ollama" and output_mode == "schema_json"
        base_prompt = self.ollama_compact_system_prompt if use_compact_prompt else self.system_prompt
        if memory_context:
            return f"{base_prompt}\n\n{memory_context}"
        return base_prompt

    @staticmethod
    def _extract_latest_user_text(messages: List[Dict[str, Any]]) -> str:
        for msg in reversed(list(messages or [])):
            if str(msg.get("role") or "").strip() == "user":
                return str(msg.get("content") or "").strip()
        if messages:
            return str(messages[-1].get("content") or "").strip()
        return ""

    @staticmethod
    def _looks_like_long_form_request(user_text: str) -> bool:
        text = str(user_text or "").strip()
        if not text:
            return False

        keywords = [
            "写一篇",
            "写文章",
            "写作文",
            "写文案",
            "详细",
            "展开",
            "解释一下",
            "分析一下",
            "总结一下",
            "讲故事",
            "讲个故事",
            "朗读",
            "读一篇",
            "演讲",
            "方案",
            "报告",
        ]
        return any(k in text for k in keywords)

    def _build_ollama_generation_options(
        self,
        *,
        messages: List[Dict[str, Any]],
        max_tokens: int,
        output_mode: str,
        tool_defs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        user_text = self._extract_latest_user_text(messages)
        token_cap = max(64, min(int(max_tokens or 128), 512))
        is_long_form = self._looks_like_long_form_request(user_text)
        has_tools = bool(tool_defs)

        if output_mode in {"json", "schema_json"}:
            if is_long_form:
                num_predict = min(token_cap, 256)
            elif has_tools:
                num_predict = min(token_cap, 128)
            else:
                num_predict = min(token_cap, 160)
            temperature = 0.1
        else:
            if is_long_form:
                num_predict = min(token_cap, 384)
                temperature = 0.4
            elif has_tools:
                num_predict = min(token_cap, 192)
                temperature = 0.2
            else:
                num_predict = min(token_cap, 128)
                temperature = 0.3

        return {
            "temperature": float(temperature),
            "num_predict": max(64, int(num_predict)),
        }

    @staticmethod
    def _build_ollama_route_schema(tool_defs: List[Dict[str, Any]]) -> Dict[str, Any]:
        tool_names = sorted(
            {
                str(item.get("name") or "").strip()
                for item in (tool_defs or [])
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            }
        )
        action_item: Dict[str, Any] = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工具名"},
                "arguments": {
                    "type": "object",
                    "description": "工具参数对象",
                    "additionalProperties": True,
                },
            },
            "required": ["name", "arguments"],
            "additionalProperties": False,
        }

        if tool_names:
            action_item["properties"]["name"]["enum"] = tool_names
            actions_schema: Dict[str, Any] = {
                "type": "array",
                "items": action_item,
                "description": "需要执行的动作；没有动作时返回 []",
            }
        else:
            actions_schema = {
                "type": "array",
                "items": action_item,
                "maxItems": 0,
                "description": "当前无可用工具，必须返回 []",
            }

        return {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["ask", "act", "both"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "say": {"type": "string", "description": "给用户朗读的文本"},
                "actions": actions_schema,
                "reason": {"type": "string", "description": "简短原因，可为空"},
            },
            "required": ["mode", "confidence", "say", "actions"],
            "additionalProperties": False,
        }

    @classmethod
    def _normalize_ollama_route_payload(cls, payload: Dict[str, Any], tool_defs: List[Dict[str, Any]]) -> Dict[str, Any]:
        allowed_names = {
            str(item.get("name") or "").strip()
            for item in (tool_defs or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }

        actions: List[Dict[str, Any]] = []
        raw_actions = payload.get("actions") if isinstance(payload, dict) else None
        if isinstance(raw_actions, list):
            for item in raw_actions[:3]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                if allowed_names and name not in allowed_names:
                    continue
                arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
                actions.append({"name": name, "arguments": arguments})

        raw_mode = str(payload.get("mode") or "").strip().lower()
        if raw_mode not in {"ask", "act", "both"}:
            raw_mode = "act" if actions else "ask"
        if actions and raw_mode == "ask":
            raw_mode = "both"
        if (not actions) and raw_mode in {"act", "both"}:
            raw_mode = "ask"

        confidence = payload.get("confidence")
        try:
            confidence_value = max(0.0, min(1.0, float(confidence)))
        except Exception:
            confidence_value = 0.9 if actions else 0.6

        say = cls._stringify_message_content(payload.get("say")).strip()
        if not say and actions:
            say = "好呀，我来处理。"
        elif not say:
            say = "好的。"

        reason = str(payload.get("reason") or "").strip()
        intent: Dict[str, Any] = {
            "mode": raw_mode,
            "confidence": confidence_value,
            "actions": actions,
        }
        if reason:
            intent["reason"] = reason[:60]

        return {"intent": intent, "say": say}

    @classmethod
    def _build_structured_text_from_tool_calls(cls, tool_calls: List[Dict[str, Any]]) -> Optional[str]:
        actions: List[Dict[str, Any]] = []
        first_name = ""
        first_args: Dict[str, Any] = {}
        for tc in tool_calls[:3]:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str(fn.get("name") or tc.get("name") or "").strip()
            if not name:
                continue
            args_raw = fn.get("arguments") if isinstance(fn, dict) else tc.get("arguments")
            args: Dict[str, Any] = {}
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw)
                except Exception:
                    args = {}
            elif isinstance(args_raw, dict):
                args = dict(args_raw)
            actions.append({"name": name, "arguments": args})
            if not first_name:
                first_name = name
                first_args = args

        if not actions:
            return None

        say = "好呀，我来处理。"
        if first_name == "music_ui":
            action = str(first_args.get("action") or "").strip()
            query = str(first_args.get("query") or "").strip()
            if action == "search" and query:
                say = f"我可以用酷狗搜索并播放“{query}”。这需要你确认一下。"
            elif action == "favorites_first":
                say = "我可以在酷狗打开我喜欢并播放一首歌。这需要你确认一下。"
        elif first_name == "play_music":
            source = str(first_args.get("source") or "kugou").strip() or "kugou"
            say = f"好，我先打开{source}开始播放。"

        intent = {
            "mode": "both",
            "confidence": 0.9,
            "actions": actions,
            "reason": "tool_calls_fallback",
        }
        return f"INTENT_JSON: {json.dumps(intent, ensure_ascii=False)}\nSAY: {say}"

    @classmethod
    def _adapt_ollama_structured_route_result(cls, raw_resp: Dict[str, Any], tool_defs: List[Dict[str, Any]]) -> Dict[str, Any]:
        provider_meta = raw_resp.get("providerMeta") if isinstance(raw_resp.get("providerMeta"), dict) else {}
        tool_calls = raw_resp.get("toolCalls") if isinstance(raw_resp.get("toolCalls"), list) else []
        raw_text = cls._stringify_message_content(raw_resp.get("text"))
        try:
            payload = json.loads(raw_text)
        except Exception:
            fallback_text = cls._build_structured_text_from_tool_calls(tool_calls)
            if fallback_text:
                return {
                    **raw_resp,
                    "text": fallback_text,
                    "toolCalls": tool_calls,
                    "providerMeta": {
                        **provider_meta,
                        "structuredRoute": True,
                        "structuredRouteParsed": False,
                        "structuredRouteFallback": "tool_calls",
                    },
                }
            return {
                **raw_resp,
                "providerMeta": {
                    **provider_meta,
                    "structuredRoute": True,
                    "structuredRouteParsed": False,
                },
            }

        if not isinstance(payload, dict):
            fallback_text = cls._build_structured_text_from_tool_calls(tool_calls)
            if fallback_text:
                return {
                    **raw_resp,
                    "text": fallback_text,
                    "toolCalls": tool_calls,
                    "providerMeta": {
                        **provider_meta,
                        "structuredRoute": True,
                        "structuredRouteParsed": False,
                        "structuredRouteFallback": "tool_calls",
                    },
                }
            return {
                **raw_resp,
                "providerMeta": {
                    **provider_meta,
                    "structuredRoute": True,
                    "structuredRouteParsed": False,
                },
            }

        normalized = cls._normalize_ollama_route_payload(payload, tool_defs)
        intent = normalized["intent"]
        say = normalized["say"]
        formatted_text = f"INTENT_JSON: {json.dumps(intent, ensure_ascii=False)}\nSAY: {say}"
        return {
            **raw_resp,
            "text": formatted_text,
            "toolCalls": tool_calls,
            "providerMeta": {
                **provider_meta,
                "structuredRoute": True,
                "structuredRouteParsed": True,
            },
        }

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
    def _build_openai_tool_defs(tool_defs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{"type": "function", "function": t} for t in tool_defs]

    @classmethod
    def _prepare_ollama_messages(cls, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将内部统一消息结构适配为 Ollama 原生 `/api/chat` 可接受的格式。"""

        prepared: List[Dict[str, Any]] = []
        for raw_msg in list(messages or []):
            if not isinstance(raw_msg, dict):
                continue

            msg = dict(raw_msg)
            tool_calls = msg.get("tool_calls") if isinstance(msg.get("tool_calls"), list) else None
            if msg.get("role") == "assistant" and tool_calls is not None:
                normalized_tool_calls: List[Dict[str, Any]] = []
                for raw_tc in tool_calls:
                    if not isinstance(raw_tc, dict):
                        continue
                    tc = dict(raw_tc)
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else None
                    if isinstance(fn, dict):
                        fn_copy = dict(fn)
                        args_raw = fn_copy.get("arguments")
                        if isinstance(args_raw, str) and args_raw.strip():
                            try:
                                parsed_args = json.loads(args_raw)
                                if isinstance(parsed_args, (dict, list)):
                                    fn_copy["arguments"] = parsed_args
                            except Exception:
                                logger.debug("ollama tool-loop arguments 反序列化失败，保留原始字符串")
                        tc["function"] = fn_copy
                    normalized_tool_calls.append(tc)
                msg["tool_calls"] = normalized_tool_calls
            prepared.append(msg)
        return prepared

    async def _invoke_dashscope_chat_completions(
        self,
        *,
        used_model: str,
        messages: List[Dict[str, str]],
        tool_defs: List[Dict[str, Any]],
        max_tokens: int,
        parallel_tool_calls: bool,
        sys_content: str,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": used_model,
            "messages": [{"role": "system", "content": sys_content}, *messages],
            "temperature": 0.7,
            "max_tokens": max_tokens,
        }

        # tools=None 表示默认工具；tools=[] 表示显式禁用。
        if tool_defs:
            payload["tools"] = self._build_openai_tool_defs(tool_defs)
            payload["tool_choice"] = "auto"
            if parallel_tool_calls:
                payload["parallel_tool_calls"] = True

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(base_url=self.base_url, timeout=60.0) as client:
            resp = await client.post("/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}

        return {
            "text": self._stringify_message_content(msg.get("content")),
            "toolCalls": OllamaClient._normalize_tool_calls(msg.get("tool_calls")),
            "model": str(data.get("model") or used_model),
            "usage": data.get("usage"),
            "raw": data,
            "provider": "dashscope",
        }

    async def _invoke_ollama_api_chat(
        self,
        *,
        used_model: str,
        messages: List[Dict[str, str]],
        tool_defs: List[Dict[str, Any]],
        max_tokens: int,
        sys_content: str,
        response_schema: Optional[Dict[str, Any]],
        output_mode: str,
    ) -> Dict[str, Any]:
        if not self.ollama_client:
            raise RuntimeError("Ollama client 未初始化")

        if output_mode == "schema_json" and not response_schema:
            raise ValueError("output_mode=schema_json 时必须提供 response_schema")

        ollama_format: Optional[Any] = None
        if output_mode == "json":
            ollama_format = "json"
        elif output_mode == "schema_json":
            ollama_format = response_schema

        options = self._build_ollama_generation_options(
            messages=messages,
            max_tokens=max_tokens,
            output_mode=output_mode,
            tool_defs=tool_defs,
        )

        prepared_messages = self._prepare_ollama_messages([{"role": "system", "content": sys_content}, *messages])

        result = await self.ollama_client.chat(
            model=used_model,
            messages=prepared_messages,
            stream=True,
            keep_alive="10m",
            options=options,
            tools=self._build_openai_tool_defs(tool_defs) if tool_defs else None,
            response_format=ollama_format,
            think=False if output_mode in {"json", "schema_json"} else None,
        )

        raw_usage = None
        if isinstance(result.raw, dict):
            prompt_eval_count = result.raw.get("prompt_eval_count")
            eval_count = result.raw.get("eval_count")
            if prompt_eval_count is not None or eval_count is not None:
                raw_usage = {
                    "prompt_tokens": prompt_eval_count,
                    "completion_tokens": eval_count,
                    "total_tokens": (prompt_eval_count or 0) + (eval_count or 0),
                }

        return {
            "text": result.content,
            "thinking": result.thinking,
            "toolCalls": result.tool_calls,
            "model": result.model or used_model,
            "usage": raw_usage,
            "raw": result.raw,
            "provider": "ollama",
            "providerMeta": {
                "done": result.done,
                "doneReason": result.done_reason,
                "format": ollama_format,
                "stream": bool(result.stream),
                "timeoutSec": float(getattr(self, "ollama_timeout_sec", 300.0)),
                "firstChunkMs": result.first_chunk_ms,
                "durationMs": result.duration_ms,
                "thinkingChars": len(str(result.thinking or "")),
            },
        }

    async def invoke_llm(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 512,
        parallel_tool_calls: bool = False,
        memory_context: str = "",
        response_schema: Optional[Dict[str, Any]] = None,
        output_mode: str = "text",
    ) -> Dict[str, Any]:
        if self.stub_enabled:
            return self._invoke_llm_stub(messages)

        used_model = model or self.model_default
        # tools=None 表示使用默认工具；tools=[] 表示显式禁用工具（例如让模型只总结工具结果）。
        tool_defs = self.function_definitions if tools is None else tools

        if self.provider == "ollama":
            ollama_output_mode = output_mode
            ollama_response_schema = response_schema
            structured_route_enabled = False

            if output_mode == "text":
                ollama_output_mode = "schema_json"
                ollama_response_schema = self._build_ollama_route_schema(tool_defs)
                structured_route_enabled = True

            sys_content = self._build_system_content(memory_context=memory_context, output_mode=ollama_output_mode)
            response = await self._invoke_ollama_api_chat(
                used_model=used_model,
                messages=messages,
                tool_defs=tool_defs,
                max_tokens=max_tokens,
                sys_content=sys_content,
                response_schema=ollama_response_schema,
                output_mode=ollama_output_mode,
            )
            if structured_route_enabled:
                return self._adapt_ollama_structured_route_result(response, tool_defs)
            return response

        sys_content = self._build_system_content(memory_context=memory_context, output_mode=output_mode)
        return await self._invoke_dashscope_chat_completions(
            used_model=used_model,
            messages=messages,
            tool_defs=tool_defs,
            max_tokens=max_tokens,
            parallel_tool_calls=parallel_tool_calls,
            sys_content=sys_content,
        )

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
