from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional

import httpx

from backend_py.config import settings
from backend_py.utils.wav import create_wav_header


logger = logging.getLogger("backend_py.tts")


class TTSService:
    """TTS 服务。

    当前实现：使用 DashScope OpenAI 兼容模式 + `qwen3-omni-flash-2025-12-01` 输出音频。

    设计目标：
    - 不改变现有 Socket.IO 音频下发协议（仍下发 WAV bytes）
    - 支持 `cancel_event` 及时停止，避免“点了停止仍继续播”
    - 优先保证“读稿一致性”：尽量朗读传入的文本，不改写、不新增
    """

    _BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    _MODEL = "qwen3-omni-flash-2025-12-01"

    # 官方示例中音频采样率为 24000，这里与其对齐。
    _SAMPLE_RATE = 24000
    _BITS_PER_SAMPLE = 16
    _CHANNELS = 1

    _TTS_SYSTEM_PROMPT = (
        "你是一个语音合成引擎（TTS）。你的唯一任务是把指定文本朗读成语音。\n"
        "规则（必须严格遵守）：\n"
        "1) 只朗读 <READ_TEXT> 与 </READ_TEXT> 标签之间的内容，逐字逐句朗读。\n"
        "2) 禁止添加、删减、改写、补全、解释或回答问题。\n"
        "3) 禁止输出任何不在标签内的内容（包括寒暄、提示语、标点替换等）。\n"
        "4) 若无法严格遵守以上规则，请输出空内容（不要编造）。"
    )

    # 仅用于前端展示与选择；真正的 `voice` 参数使用 `id` 字段。
    _OMNI_VOICES: list[dict[str, Any]] = [
        {"id": "Cherry", "name": "芊悦", "gender": "female", "desc": "阳光积极、亲切自然小姐姐"},
        {"id": "Serena", "name": "苏瑶", "gender": "female", "desc": "温柔小姐姐"},
        {"id": "Ethan", "name": "晨煦", "gender": "male", "desc": "标准普通话，阳光温暖"},
        {"id": "Chelsie", "name": "千雪", "gender": "female", "desc": "二次元虚拟女友"},
        {"id": "Momo", "name": "茉兔", "gender": "female", "desc": "撒娇搞怪，逗你开心"},
        {"id": "Vivian", "name": "十三", "gender": "female", "desc": "拽拽的、可爱的小暴躁"},
        {"id": "Moon", "name": "月白", "gender": "male", "desc": "率性帅气的月白"},
        {"id": "Maia", "name": "四月", "gender": "female", "desc": "知性与温柔的碰撞"},
        {"id": "Kai", "name": "凯", "gender": "male", "desc": "耳朵的一场SPA"},
        {"id": "Nofish", "name": "不吃鱼", "gender": "male", "desc": "不会翘舌音的设计师"},
        {"id": "Bella", "name": "萌宝", "gender": "female", "desc": "喝酒不打醉拳的小萝莉"},
        {"id": "Jennifer", "name": "詹妮弗", "gender": "female", "desc": "品牌级、电影质感般美语女声"},
        {"id": "Ryan", "name": "甜茶", "gender": "male", "desc": "节奏拉满，戏感炸裂"},
        {"id": "Katerina", "name": "卡捷琳娜", "gender": "female", "desc": "御姐音色，韵律回味十足"},
        {"id": "Aiden", "name": "艾登", "gender": "male", "desc": "精通厨艺的美语大男孩"},
        {"id": "Eldric Sage", "name": "沧明子", "gender": "male", "desc": "沉稳睿智的老者"},
        {"id": "Mia", "name": "乖小妹", "gender": "female", "desc": "温顺乖巧"},
        {"id": "Mochi", "name": "沙小弥", "gender": "female", "desc": "聪明伶俐的小大人"},
        {"id": "Bellona", "name": "燕铮莺", "gender": "female", "desc": "声音洪亮，吐字清晰"},
        {"id": "Vincent", "name": "田叔", "gender": "male", "desc": "沙哑烟嗓"},
        {"id": "Bunny", "name": "萌小姬", "gender": "female", "desc": "萌属性小萝莉"},
        {"id": "Neil", "name": "阿闻", "gender": "male", "desc": "专业新闻主持人"},
        {"id": "Elias", "name": "墨讲师", "gender": "male", "desc": "严谨又会讲故事"},
        {"id": "Arthur", "name": "徐大爷", "gender": "male", "desc": "质朴嗓音"},
        {"id": "Nini", "name": "邻家妹妹", "gender": "female", "desc": "又软又黏的嗓音"},
        {"id": "Ebona", "name": "诡婆婆", "gender": "female", "desc": "低语恐怖氛围"},
        {"id": "Seren", "name": "小婉", "gender": "female", "desc": "助眠声线"},
        {"id": "Pip", "name": "顽屁小孩", "gender": "male", "desc": "调皮童真"},
        {"id": "Stella", "name": "少女阿月", "gender": "female", "desc": "甜到发腻的少女音"},
        {"id": "Bodega", "name": "博德加", "gender": "male", "desc": "热情西班牙大叔"},
        {"id": "Sonrisa", "name": "索尼莎", "gender": "female", "desc": "热情开朗拉美大姐"},
        {"id": "Alek", "name": "阿列克", "gender": "male", "desc": "战斗民族的冷与暖"},
        {"id": "Dolce", "name": "多尔切", "gender": "male", "desc": "慵懒意大利大叔"},
        {"id": "Sohee", "name": "素熙", "gender": "female", "desc": "温柔开朗韩国欧尼"},
        {"id": "Ono Anna", "name": "小野杏", "gender": "female", "desc": "鬼灵精怪的青梅竹马"},
        {"id": "Lenn", "name": "莱恩", "gender": "male", "desc": "德国青年"},
        {"id": "Emilien", "name": "埃米尔安", "gender": "male", "desc": "浪漫法国大哥哥"},
        {"id": "Andre", "name": "安德雷", "gender": "male", "desc": "沉稳舒服男生"},
        {"id": "Radio Gol", "name": "拉迪奥·戈尔", "gender": "male", "desc": "足球诗人解说"},
        {"id": "Jada", "name": "上海-阿珍", "gender": "female", "desc": "风风火火沪上阿姐"},
        {"id": "Dylan", "name": "北京-晓东", "gender": "male", "desc": "北京胡同少年"},
        {"id": "Li", "name": "南京-老李", "gender": "male", "desc": "耐心瑜伽老师"},
        {"id": "Marcus", "name": "陕西-秦川", "gender": "male", "desc": "老陕味道"},
        {"id": "Roy", "name": "闽南-阿杰", "gender": "male", "desc": "市井活泼"},
        {"id": "Peter", "name": "天津-李彼得", "gender": "male", "desc": "天津相声捧哏"},
        {"id": "Sunny", "name": "四川-晴儿", "gender": "female", "desc": "甜甜川妹子"},
        {"id": "Eric", "name": "四川-程川", "gender": "male", "desc": "成都男子"},
        {"id": "Rocky", "name": "粤语-阿强", "gender": "male", "desc": "幽默风趣陪聊"},
        {"id": "Kiki", "name": "粤语-阿清", "gender": "female", "desc": "甜美港妹闺蜜"},
    ]

    def __init__(self) -> None:
        self.api_key = settings.dashscope_api_key
        if not self.api_key:
            logger.warning("未设置DASHSCOPE_API_KEY环境变量，TTS功能将不可用")

    @staticmethod
    def map_voice_from_gender(gender: str) -> str:
        """兼容旧前端：仍然支持以 gender 选择音色。"""

        return {"female": "Cherry", "male": "Ethan"}.get(str(gender or "").lower(), "Cherry")

    def get_available_voices(self) -> list[dict[str, Any]]:
        return [{**v, "provider": "qwen_omni"} for v in self._OMNI_VOICES]

    @staticmethod
    async def _iter_sse_json(resp: httpx.Response) -> AsyncIterator[Dict[str, Any]]:
        """迭代 OpenAI 兼容 SSE 的 `data: {...}` JSON 块。"""

        async for line in resp.aiter_lines():
            if not line:
                continue

            stripped = line.strip()
            if not stripped:
                continue

            if not stripped.startswith("data:"):
                continue

            payload = stripped[len("data:") :].strip()
            if not payload or payload == "[DONE]":
                break

            try:
                obj = json.loads(payload)
            except Exception:
                continue

            if isinstance(obj, dict):
                yield obj

    @staticmethod
    def _take_base64_decodable_prefix(buf: str) -> tuple[str, str]:
        """从 buf 前缀切出可 base64 解码的部分（长度为 4 的倍数）。"""

        usable_len = (len(buf) // 4) * 4
        if usable_len <= 0:
            return ("", buf)
        return (buf[:usable_len], buf[usable_len:])

    @staticmethod
    def _wrap_read_text(text: str) -> str:
        """为 TTS 读稿增加边界标签，降低模型扩写/改写概率。"""

        # 注意：保持原文不变，仅包裹标签。
        return f"<READ_TEXT>\n{text}\n</READ_TEXT>"

    @staticmethod
    def _preview(text: str, *, limit: int = 80) -> str:
        t = str(text or "")
        t = t.replace("\n", "\\n")
        if len(t) <= limit:
            return t
        return t[:limit] + "..."

    @staticmethod
    def _normalize_for_compare(text: str) -> str:
        """用于一致性对账的轻度归一化。

        只移除所有空白字符，避免换行/空格差异造成误报；若模型真的扩写，仍能被检测到。
        """

        return "".join(str(text or "").split())

    def _build_payload(self, *, text: str, voice: str) -> Dict[str, Any]:
        """构造 Omni TTS 请求 payload。

        单独拆出来便于：
        - 统一设置低随机性参数（减少扩写）
        - 便于离线脚本做结构自检
        """

        wrapped = self._wrap_read_text(str(text or ""))
        return {
            "model": self._MODEL,
            "messages": [
                {"role": "system", "content": self._TTS_SYSTEM_PROMPT},
                {"role": "user", "content": wrapped},
            ],
            "modalities": ["text", "audio"],
            "audio": {"voice": voice, "format": "wav"},
            "stream": True,
            "stream_options": {"include_usage": True},
            # 低随机性：尽量让“朗读”行为确定，减少模型自行发挥。
            "temperature": 0,
            "top_p": 1,
        }

    async def text_to_speech(
        self,
        text: str,
        voice_settings: Dict[str, Any],
        *,
        on_audio_chunk: Optional[Callable[[bytes], Awaitable[None]]] = None,
        cancel_event: Optional[asyncio.Event] = None,
        request_id: Optional[str] = None,
    ) -> bytes:
        if not self.api_key:
            return b""

        cancel = cancel_event or asyncio.Event()

        # 新链路使用 `voice` 参数；为兼容旧前端，允许从 `model` 字段透传。
        raw_voice = (
            str(voice_settings.get("voice") or "").strip()
            or str(voice_settings.get("model") or "").strip()
            or self.map_voice_from_gender(str(voice_settings.get("gender") or "female"))
        )

        # 如果前端仍传旧 sambert 模型名，降级回默认音色。
        voice = raw_voice
        if raw_voice.startswith("sambert-"):
            voice = self.map_voice_from_gender(str(voice_settings.get("gender") or "female"))

        input_text = str(text or "")
        payload = self._build_payload(text=input_text, voice=voice)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        full_pcm_parts: list[bytes] = []
        pending_pcm_parts: list[bytes] = []
        pending_pcm_size = 0

        # 对账：模型在流式响应里可能同时返回 delta.content（它“认为自己说了什么”）。
        # 我们累积它用于排障（当听到的内容与 UI 文本不一致时）。
        spoken_text_parts: list[str] = []

        base64_buf = ""
        have_audio = False

        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)

        try:
            async with httpx.AsyncClient(base_url=self._BASE_URL, timeout=timeout) as client:
                async with client.stream("POST", "/chat/completions", headers=headers, json=payload) as resp:
                    dashscope_req_id = resp.headers.get("X-DashScope-Request-Id", "")
                    logger.info(
                        "TTS请求开始 model=%s voice=%s requestId=%s dashscopeRequestId=%s",
                        self._MODEL,
                        voice,
                        request_id,
                        dashscope_req_id,
                    )

                    resp.raise_for_status()

                    async for obj in self._iter_sse_json(resp):
                        if cancel.is_set():
                            logger.info(
                                "TTS canceled; stop streaming requestId=%s dashscopeRequestId=%s",
                                request_id,
                                dashscope_req_id,
                            )
                            break

                        choices = obj.get("choices") or []
                        if not choices:
                            continue

                        delta = (choices[0].get("delta") or {}) if isinstance(choices[0], dict) else {}

                        # 记录模型文本增量（用于对账/排障，不影响音频下发）。
                        if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                            spoken_text_parts.append(delta.get("content") or "")

                        audio_delta = delta.get("audio") if isinstance(delta, dict) else None
                        if isinstance(audio_delta, dict) and isinstance(audio_delta.get("data"), str):
                            have_audio = True
                            base64_buf += audio_delta.get("data") or ""

                            decodable, base64_buf = self._take_base64_decodable_prefix(base64_buf)
                            if decodable:
                                pcm = base64.b64decode(decodable)
                                if pcm:
                                    full_pcm_parts.append(pcm)
                                    pending_pcm_parts.append(pcm)
                                    pending_pcm_size += len(pcm)

                                    # 首包尽量快一点，后续适当加大 chunk，减少 decodeAudioData 压力
                                    threshold = 4000 if len(full_pcm_parts) <= 1 else 12000
                                    if on_audio_chunk and pending_pcm_size >= threshold:
                                        pcm_chunk = b"".join(pending_pcm_parts)
                                        wav = (
                                            create_wav_header(
                                                len(pcm_chunk),
                                                sample_rate=self._SAMPLE_RATE,
                                                bits_per_sample=self._BITS_PER_SAMPLE,
                                                channels=self._CHANNELS,
                                            )
                                            + pcm_chunk
                                        )
                                        await on_audio_chunk(wav)
                                        pending_pcm_parts = []
                                        pending_pcm_size = 0

                    # flush remaining PCM
                    if on_audio_chunk and pending_pcm_parts and not cancel.is_set():
                        pcm_chunk = b"".join(pending_pcm_parts)
                        wav = (
                            create_wav_header(
                                len(pcm_chunk),
                                sample_rate=self._SAMPLE_RATE,
                                bits_per_sample=self._BITS_PER_SAMPLE,
                                channels=self._CHANNELS,
                            )
                            + pcm_chunk
                        )
                        await on_audio_chunk(wav)

        except httpx.HTTPStatusError as e:
            dashscope_req_id = ""
            try:
                dashscope_req_id = e.response.headers.get("X-DashScope-Request-Id", "")
            except Exception:
                dashscope_req_id = ""

            logger.error(
                "TTS请求失败 status=%s requestId=%s dashscopeRequestId=%s body=%s",
                getattr(getattr(e, "response", None), "status_code", None),
                request_id,
                dashscope_req_id,
                (getattr(getattr(e, "response", None), "text", "") or "")[:300],
            )
            return b""
        except Exception as e:
            logger.exception("TTS异常 requestId=%s err=%s", request_id, e)
            return b""

        # 尽量把残余 base64 解掉（补齐 padding），避免音频尾部丢失。
        if have_audio and base64_buf and not cancel.is_set():
            try:
                padded = base64_buf + ("=" * ((4 - (len(base64_buf) % 4)) % 4))
                pcm_tail = base64.b64decode(padded)
                if pcm_tail:
                    full_pcm_parts.append(pcm_tail)
            except Exception:
                pass

        spoken_text = "".join(spoken_text_parts).strip()
        if spoken_text and not cancel.is_set():
            norm_in = self._normalize_for_compare(input_text)
            norm_spoken = self._normalize_for_compare(spoken_text)
            if norm_in and norm_spoken and norm_in != norm_spoken:
                logger.warning(
                    "TTS文本不一致 requestId=%s voice=%s inputLen=%d spokenLen=%d input=%s spoken=%s",
                    request_id,
                    voice,
                    len(input_text),
                    len(spoken_text),
                    self._preview(input_text),
                    self._preview(spoken_text),
                )
            else:
                logger.info(
                    "TTS文本对账一致 requestId=%s voice=%s len=%d",
                    request_id,
                    voice,
                    len(input_text),
                )

        full_pcm = b"".join(full_pcm_parts)
        if not full_pcm or cancel.is_set():
            return b""

        return (
            create_wav_header(
                len(full_pcm),
                sample_rate=self._SAMPLE_RATE,
                bits_per_sample=self._BITS_PER_SAMPLE,
                channels=self._CHANNELS,
            )
            + full_pcm
        )
