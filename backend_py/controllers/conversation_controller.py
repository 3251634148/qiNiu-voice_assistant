from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import socketio

from backend_py.safety import RiskAssessment, SafetyService
from backend_py.services.asr_service import ASRService
from backend_py.services.device_location_service import DeviceLocationService
from backend_py.services.llm_service import LLMService
from backend_py.services.memory_service import MemoryService
from backend_py.services.network_tools_service import NetworkToolsService
from backend_py.services.system_controller import SystemController
from backend_py.services.tool_router import ToolRouter
from backend_py.services.tts_service import TTSService
from backend_py.session_store import SessionStore


logger = logging.getLogger("backend_py.controller")


class ConversationController:
    def __init__(self, *, sio: socketio.AsyncServer) -> None:
        self.sio = sio
        self.session_store = SessionStore()
        self.safety_service = SafetyService()
        self.system_controller = SystemController()
        self.llm_service = LLMService()
        self.network_tools_service = NetworkToolsService()
        self.device_location_service = DeviceLocationService()
        self.tts_service = TTSService()
        self.asr_service = ASRService()
        self.memory_service = MemoryService()
        self.tool_router = ToolRouter(llm_service=self.llm_service)

        # 以“会话 id”维度管理 TTS 任务：
        # - 若客户端注册了 clientId，则会话 id 为 clientId（跨重连稳定）
        # - 否则会话 id 为 socket sid
        self._tts_tasks: Dict[str, asyncio.Task] = {}

        # socket sid -> clientId 映射（用于把“配置/权限/历史”绑定到稳定 clientId）
        self._sid_to_client_id: Dict[str, str] = {}

        # clientId -> active connection count（用于热键目标选择与 fallback）
        self._client_active_counts: Dict[str, int] = {}
        self._last_registered_client_id: Optional[str] = None

        self.connected_count = 0

    @staticmethod
    def _client_room(client_id: str) -> str:
        return f"client:{client_id}"

    def register_client(self, sid: str, *, client_id: str) -> None:
        """绑定 socket sid 到稳定 clientId。

        绑定后：
        - session 的 key 由 sid 迁移为 clientId
        - 后续 emit 使用 room=client:{clientId}，而不是 to=sid
        """
        client_id_s = str(client_id or "").strip()
        if not client_id_s:
            return

        prev_client_id = self._sid_to_client_id.get(sid)
        if prev_client_id and prev_client_id != client_id_s:
            self._client_active_counts[prev_client_id] = max(0, int(self._client_active_counts.get(prev_client_id, 1)) - 1)
            if self._client_active_counts.get(prev_client_id, 0) <= 0:
                self._client_active_counts.pop(prev_client_id, None)

        self._sid_to_client_id[sid] = client_id_s
        self._client_active_counts[client_id_s] = int(self._client_active_counts.get(client_id_s, 0)) + 1
        self._last_registered_client_id = client_id_s

        self.session_store.migrate(sid, client_id_s)

        task = self._tts_tasks.pop(sid, None)
        if task is not None:
            self._tts_tasks[client_id_s] = task

    def resolve_hotkey_emit_to(self, preferred_client_id: str) -> Tuple[str, str]:
        """为热键请求选择一个可投递的 emit_to。

        选择策略：
        1) 若 preferred_client_id 在线（count>0），则选它。
        2) 否则选最近一次注册且仍在线的 clientId。
        3) 否则选任意一个在线 clientId（按字典序稳定选择）。
        4) 若无任何在线 clientId，则仍返回 preferred 的 room（此时会静默丢弃），并记录警告。
        """
        preferred = str(preferred_client_id or "").strip() or "desktop"

        if int(self._client_active_counts.get(preferred, 0)) > 0:
            return preferred, self._client_room(preferred)

        last = str(self._last_registered_client_id or "").strip()
        if last and int(self._client_active_counts.get(last, 0)) > 0:
            logger.warning("热键目标 clientId=%s 不在线，fallback 到最近在线 clientId=%s", preferred, last)
            return last, self._client_room(last)

        online = sorted([cid for cid, n in self._client_active_counts.items() if int(n) > 0])
        if online:
            chosen = online[0]
            logger.warning("热键目标 clientId=%s 不在线，fallback 到在线 clientId=%s", preferred, chosen)
            return chosen, self._client_room(chosen)

        logger.warning("热键目标 clientId=%s 不在线且当前无任何在线 clientId，无法投递回复", preferred)
        return preferred, self._client_room(preferred)

    def _resolve_session_and_emit_to(self, sid: str) -> Tuple[str, str]:
        """将输入 sid 解析为 session_id 与 emit_to。

        支持两种 sid 形态：
        - socket sid（来自客户端连接）
        - room sid："client:<clientId>"（用于热键等无 socket sid 的场景）
        """
        raw = str(sid or "").strip()
        if raw.startswith("client:"):
            client_id = raw.split(":", 1)[1].strip()
            if client_id:
                return client_id, raw

        client_id = self._sid_to_client_id.get(raw)
        if client_id:
            return client_id, self._client_room(client_id)

        return raw, raw
    def get_supported_tool_names(self) -> list[str]:
        return [t["name"] for t in self.tool_router.get_supported_tools()]

    def on_connect(self, sid: str) -> None:
        self.connected_count += 1

    def on_disconnect(self, sid: str) -> None:
        self.connected_count = max(0, self.connected_count - 1)

        session_id, _emit_to = self._resolve_session_and_emit_to(sid)
        self.stop_tts(session_id)

        # 若该 sid 绑定了 clientId，则不清理会话（保证重连后配置/历史仍在）。
        if sid in self._sid_to_client_id:
            client_id = self._sid_to_client_id.pop(sid, None)
            if client_id:
                self._client_active_counts[client_id] = max(0, int(self._client_active_counts.get(client_id, 1)) - 1)
                if self._client_active_counts.get(client_id, 0) <= 0:
                    self._client_active_counts.pop(client_id, None)
            return

        self.session_store.clear(session_id)
        self._tts_tasks.pop(session_id, None)

    @staticmethod
    def _coerce_audio_bytes(audio_data: Any) -> bytes:
        if audio_data is None:
            return b""
        if isinstance(audio_data, (bytes, bytearray)):
            return bytes(audio_data)
        if isinstance(audio_data, list) and all(isinstance(x, int) for x in audio_data):
            return bytes(audio_data)
        if isinstance(audio_data, str):
            # 部分客户端可能会发送 base64 字符串
            try:
                import base64

                return base64.b64decode(audio_data)
            except Exception:
                return audio_data.encode("utf-8", errors="ignore")
        return b""

    async def handle_voice_input(
        self,
        *,
        sid: str,
        audio_data: Any,
        language: str,
        request_id: Optional[str],
    ) -> None:
        session_id, emit_to = self._resolve_session_and_emit_to(sid)

        audio_bytes = self._coerce_audio_bytes(audio_data)
        if not audio_bytes:
            await self.sio.emit(
                "assistant-message",
                {
                    "type": "error",
                    "content": "未收到有效音频数据，请重试",
                    "requestId": request_id,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                to=emit_to,
            )
            return

        try:
            recognized = await self.asr_service.transcribe(audio_bytes, language=language)
        except Exception as e:
            logger.exception("ASR失败: %s", e)
            await self.sio.emit(
                "assistant-message",
                {
                    "type": "error",
                    "content": f"语音识别失败：{e}",
                    "requestId": request_id,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                to=emit_to,
            )
            return

        recognized_text = str(recognized or "").strip()
        if not recognized_text:
            await self.sio.emit(
                "assistant-message",
                {
                    "type": "error",
                    "content": "未识别到有效语音内容，请重试",
                    "requestId": request_id,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                to=emit_to,
            )
            return

        await self.sio.emit(
            "speech-recognized",
            {
                "text": recognized_text,
                "language": language,
                "requestId": request_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            to=emit_to,
        )

        # 注意：文本处理链路使用同一个 emit_to（room 或 socket sid）来保证结果可回传。
        await self.handle_text_command(sid=emit_to, text=recognized_text, request_id=request_id)

    def _gen_request_id(self, sid: str) -> str:
        return f"req_{sid}_{int(time.time() * 1000)}_{random.randint(100000, 999999)}"

    def reset_tts_stop(self, sid: str) -> None:
        session_id, _emit_to = self._resolve_session_and_emit_to(sid)
        session = self.session_store.get_or_create(session_id)
        session.tts_stopped = False

    def is_tts_stopped(self, sid: str) -> bool:
        session_id, _emit_to = self._resolve_session_and_emit_to(sid)
        session = self.session_store.get_or_create(session_id)
        return session.tts_stopped is True

    def stop_tts(self, sid: str) -> None:
        session_id, _emit_to = self._resolve_session_and_emit_to(sid)
        session = self.session_store.get_or_create(session_id)
        session.tts_stopped = True

        task = self._tts_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()

    def is_local_control_allowed(self, sid: str) -> bool:
        session = self.session_store.get_or_create(sid)
        allow = session.allow_local_control
        if allow is None:
            allow = session.tts_settings.get("allowLocalControl")
        return True if allow is None else bool(allow)

    def update_tts_settings(self, sid: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        try:
            session_id, _emit_to = self._resolve_session_and_emit_to(sid)
            session = self.session_store.get_or_create(session_id)
            # 兼容前端旧字段：
            # - 旧版会传 model（sambert-xxx），新版会传 voice（Cherry/Ethan/...）。
            # - 由于本次切换到 Omni TTS，sambert model 不再用于后端合成，仅保留为兼容存储字段。
            raw_voice = settings.get("voice")
            if not isinstance(raw_voice, str) or not raw_voice.strip():
                raw_voice = settings.get("model") if isinstance(settings.get("model"), str) else None

            tts = {
                "gender": settings.get("gender") or "female",
                "rate": float(settings.get("rate") or 1.0),
                "pitch": float(settings.get("pitch") or 1.0),
                "voice": str(raw_voice).strip() if raw_voice else session.tts_settings.get("voice"),
                "model": settings.get("model") or session.tts_settings.get("model"),
                "allowLocalControl": settings.get("allowLocalControl")
                if isinstance(settings.get("allowLocalControl"), bool)
                else session.tts_settings.get("allowLocalControl", True),
            }
            tts["rate"] = max(0.5, min(2.0, tts["rate"]))
            tts["pitch"] = max(0.5, min(2.0, tts["pitch"]))

            session.tts_settings = tts
            if isinstance(settings.get("allowLocalControl"), bool):
                session.allow_local_control = bool(settings.get("allowLocalControl"))

            return {"success": True, "settings": session.tts_settings}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_network_settings(self, sid: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        """更新联网开关（前端一次性授权后持久化，并同步到后端会话态）。"""

        try:
            session_id, _emit_to = self._resolve_session_and_emit_to(sid)
            session = self.session_store.get_or_create(session_id)
            enabled = settings.get("networkAccessEnabled")
            if not isinstance(enabled, bool):
                return {"success": False, "error": "networkAccessEnabled 必须是布尔值"}

            session.network_access_enabled = enabled
            # 记录“显式设置时间戳”，用于 sid->clientId 迁移时判定新旧优先级（避免时序竞争导致开关丢失）。
            try:
                session.network_access_set_at_ms = int(time.time() * 1000)  # type: ignore[attr-defined]
            except Exception:
                pass
            return {"success": True, "networkAccessEnabled": session.network_access_enabled}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_device_location(self, sid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """更新设备定位信息（更准确的当前位置）。

        说明：
        - 该能力必须由前端一次性授权后才会启用。
        - 后端只保存必要的经纬度与时间戳，不做持久化落盘。
        """

        try:
            session_id, _emit_to = self._resolve_session_and_emit_to(sid)
            session = self.session_store.get_or_create(session_id)

            enabled = payload.get("deviceLocationEnabled")
            if not isinstance(enabled, bool):
                return {"success": False, "error": "deviceLocationEnabled 必须是布尔值"}

            session.device_location_enabled = enabled
            # 记录“显式设置时间戳”，用于 sid->clientId 迁移时判定新旧优先级（避免时序竞争导致开关丢失）。
            try:
                session.device_location_set_at_ms = int(time.time() * 1000)  # type: ignore[attr-defined]
            except Exception:
                pass

            if not enabled:
                session.device_location = None
                return {"success": True, "deviceLocationEnabled": False}

            lon_lat = str(payload.get("lonLat") or "").strip()
            try:
                ts_ms = int(payload.get("tsMs") or 0)
            except Exception:
                ts_ms = 0

            # 仅作为"授权开关"同步：当 lonLat 未提供时，不报错。
            # 实际定位将由后端在 get_device_location 工具调用时实时获取。
            if not lon_lat:
                session.device_location = None
                return {"success": True, "deviceLocationEnabled": True}

            if ts_ms <= 0:
                ts_ms = int(time.time() * 1000)

            session.device_location = {"lonLat": lon_lat, "tsMs": ts_ms}
            return {
                "success": True,
                "deviceLocationEnabled": True,
                "deviceLocation": session.device_location,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_tts_settings(self, sid: str) -> Dict[str, Any]:
        try:
            session_id, _emit_to = self._resolve_session_and_emit_to(sid)
            session = self.session_store.get_or_create(session_id)
            return {
                "success": True,
                "settings": session.tts_settings
                or {"gender": "female", "rate": 1.0, "pitch": 1.0, "voice": "Cherry", "model": None},
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_available_voices(self) -> Dict[str, Any]:
        try:
            return {"success": True, "voices": self.tts_service.get_available_voices()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_session_status(self, sid: str) -> Dict[str, Any]:
        session_id, _emit_to = self._resolve_session_and_emit_to(sid)
        return self.session_store.get_status(session_id)

    def get_session_history(self, sid: str, limit: int) -> List[Dict[str, Any]]:
        session_id, _emit_to = self._resolve_session_and_emit_to(sid)
        return self.session_store.get_history(session_id, limit)

    def clear_session(self, sid: str) -> None:
        session_id, _emit_to = self._resolve_session_and_emit_to(sid)
        self.session_store.clear(session_id)
        self._tts_tasks.pop(session_id, None)

    def get_stats(self) -> Dict[str, Any]:
        return self.session_store.get_stats()

    async def get_system_info(self) -> Dict[str, Any]:
        return await self.system_controller.get_system_info()

    def normalize_intent_object(self, intent: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(intent, dict):
            return None

        mode = intent.get("mode") if isinstance(intent.get("mode"), str) else None
        conf = intent.get("confidence")
        if isinstance(conf, (int, float)):
            conf = max(0.0, min(1.0, float(conf)))
        else:
            conf = None

        actions: list[dict[str, Any]] = []
        raw_actions = intent.get("actions")
        if isinstance(raw_actions, list):
            for a in raw_actions:
                if not isinstance(a, dict):
                    continue
                name = a.get("name") if isinstance(a.get("name"), str) else None
                args = a.get("arguments") if isinstance(a.get("arguments"), dict) else {}
                if name:
                    actions.append({"name": name, "arguments": args})

        reason = intent.get("reason") if isinstance(intent.get("reason"), str) else None

        normalized: Dict[str, Any] = {"mode": mode, "confidence": conf, "actions": actions}
        if reason:
            normalized["reason"] = reason
        return normalized

    @staticmethod
    def _sanitize_say_text(text: str) -> str:
        """把模型协议输出清洗为可直接对用户展示/朗读的纯文本。

        兼容：
        - 英文/中文冒号（: / ：）
        - SAY 与冒号之间存在空格（例如 "SAY :"）
        - 输出前存在 BOM 或空白
        """

        t = str(text or "").lstrip("\ufeff").strip()
        if not t:
            return ""

        lines = t.splitlines()
        if not lines:
            return ""

        # 如果第一行还是协议行，则丢弃它。
        if re.match(r"^\s*INTENT_JSON\s*[:：]", lines[0]):
            lines = lines[1:]

        if not lines:
            return ""

        # 剥离首行 SAY 前缀（兼容中英文冒号与空格）。
        lines[0] = re.sub(r"^\s*SAY\s*[:：]\s*", "", lines[0])

        # 兜底：如果整体又以 SAY 开头（例如模型输出多了空行），再剥一次。
        out = "\n".join(lines).strip()
        out = re.sub(r"^\s*SAY\s*[:：]\s*", "", out).strip()
        return out

    def parse_assistant_text(self, raw_text: str) -> Dict[str, Any]:
        text = str(raw_text or "").lstrip("\ufeff")
        lines = text.splitlines()
        if not lines:
            return {"intent": None, "sayText": ""}

        first = lines[0]
        m = re.match(r"^\s*INTENT_JSON\s*[:：]\s*(\{.*\})\s*$", first)
        if m:
            json_part = m.group(1).strip()
            intent = None
            try:
                intent = self.normalize_intent_object(json.loads(json_part))
            except Exception:
                intent = None

            say_lines = lines[1:]
            if say_lines:
                say_lines[0] = re.sub(r"^\s*SAY\s*[:：]\s*", "", say_lines[0])

            return {"intent": intent, "sayText": self._sanitize_say_text("\n".join(say_lines))}

        if re.match(r"^\s*SAY\s*[:：]", first):
            return {"intent": None, "sayText": self._sanitize_say_text(text)}

        return {"intent": None, "sayText": self._sanitize_say_text(text)}

    def merge_intent_with_tool_calls(self, intent: Optional[Dict[str, Any]], tool_calls: Any) -> Dict[str, Any]:
        normalized = self.normalize_intent_object(intent) if intent else None
        if not normalized:
            normalized = {"mode": None, "confidence": None, "actions": []}

        calls = tool_calls if isinstance(tool_calls, list) else []

        if calls and not normalized["actions"]:
            actions = []
            for tc in calls:
                fn = tc.get("function") or {}
                name = fn.get("name") or tc.get("name")
                args_raw = fn.get("arguments")
                args = {}
                if isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw)
                    except Exception:
                        args = {}
                if isinstance(name, str) and name:
                    actions.append({"name": name, "arguments": args})
            normalized["actions"] = actions

        if not normalized.get("mode"):
            normalized["mode"] = "act" if normalized["actions"] else "ask"

        if normalized["actions"] and normalized.get("confidence") is None:
            normalized["confidence"] = 0.9

        return normalized

    def build_tool_calls_from_intent(self, intent: Dict[str, Any]) -> List[Dict[str, Any]]:
        actions = (intent or {}).get("actions")
        if not isinstance(actions, list) or not actions:
            return []

        now = int(time.time() * 1000)
        calls = []
        for idx, a in enumerate(actions[:3]):
            if not isinstance(a, dict):
                continue
            name = a.get("name")
            if not isinstance(name, str) or not name:
                continue
            args = a.get("arguments") if isinstance(a.get("arguments"), dict) else {}
            calls.append(
                {
                    "id": f"intent_{now}_{idx}_{random.randint(1000, 9999)}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                }
            )
        return calls

    @staticmethod
    def _looks_like_playback_claim(text: str) -> bool:
        t = str(text or "").strip()
        if not t:
            return False
        claims = [
            "正在为你播放",
            "正在播放",
            "我来播放",
            "我给你播放",
            "已为你播放",
            "已开始播放",
            "我已经给你放",
            "马上给你放",
        ]
        return any(c in t for c in claims)

    @staticmethod
    def _extract_music_search_query(user_text: str) -> Optional[str]:
        t = str(user_text or "").strip()
        if not t:
            return None

        # 防误判：讲故事/讲笑话/念诗/读文章/解释内容不属于播放音乐。
        if any(x in t for x in ["讲故事", "讲笑话", "念诗", "读文章", "解释"]):
            return None

        # 提取书名号/引号中的歌名。
        if "《" in t and "》" in t:
            start = t.find("《")
            end = t.find("》", start + 1)
            if start >= 0 and end > start:
                inner = t[start + 1 : end].strip()
                if inner and inner not in {"音乐", "歌曲", "歌"}:
                    return inner

        if t.startswith(""") and t.endswith(""") and len(t) >= 4:
            inner = t[1:-1].strip()
            if inner and inner not in {"音乐", "歌曲", "歌"}:
                return inner

        if t.startswith('"') and t.endswith('"') and len(t) >= 4:
            inner = t[1:-1].strip()
            if inner and inner not in {"音乐", "歌曲", "歌"}:
                return inner

        # 去掉常见的口头前缀。
        for prefix in [
            "我想听",
            "我要听",
            "帮我放",
            "给我放",
            "播放",
            "放",
            "来一首",
            "来首",
            "放一首",
            "听",
        ]:
            if t.startswith(prefix):
                t = t[len(prefix) :].strip()
                break

        if not t or t in {"音乐", "歌曲", "歌"}:
            return None

        # 识别模式："歌手的歌名"
        m = re.match(r"^(.{1,10})的(.{1,25})$", t)
        if m:
            artist = m.group(1).strip()
            song = m.group(2).strip()
            if song and song not in {"音乐", "歌曲", "歌"}:
                return f"{artist} {song}".strip()

        # 过滤已知的泛化短语（额外安全护栏）。
        generic_phrases = {
            "来点音乐",
            "来点歌",
            "放点歌",
            "听歌",
            "听音乐",
        }
        if t in generic_phrases:
            return None

        # 若仍像一个有效的搜索词，则直接返回。
        if 2 <= len(t) <= 30:
            return t

        return None

    @staticmethod
    def _is_music_request(user_text: str) -> bool:
        """判断用户是否在请求播放音乐。

        注意：这里是"后端兜底"的识别逻辑，必须尽量保守，避免越权把对话请求误判成音乐操作。
        """

        t = str(user_text or "").strip()
        if not t:
            return False

        # 强排除：对话/内容型请求不应触发音乐兜底（哪怕包含"听/讲/说"等字眼）。
        negative = [
            "故事",
            "讲故事",
            "听故事",
            "笑话",
            "段子",
            "念诗",
            "诗",
            "读文章",
            "朗读",
            "解释",
            "讲解",
            "科普",
            "翻译",
            "总结",
            "复述",
            "陪我聊",
            "聊天",
            "对话",
            "视频",
            "电影",
        ]
        if any(x in t for x in negative):
            return False

        # 正向信号：必须出现较明确的"音乐/听歌/点歌/播放一首"类表达。
        keywords = [
            "听歌",
            "听音乐",
            "放歌",
            "播放音乐",
            "来点音乐",
            "来点歌",
            "放点歌",
            "点歌",
            "随机听歌",
            "随机播放",
            "来一首",
            "来首",
            "放一首",
            "播放",
        ]
        return any(k in t for k in keywords)

    @staticmethod
    def _is_weather_request(user_text: str) -> bool:
        t = str(user_text or "").strip()
        if not t:
            return False

        keywords = [
            "天气",
            "气温",
            "温度",
            "预报",
            "下雨",
            "降雨",
            "雨",
            "湿度",
            "风",
            "体感",
            "冷不冷",
            "热不热",
        ]
        return any(k in t for k in keywords)

    @staticmethod
    def _contains_explicit_location_hint(user_text: str) -> bool:
        """判断用户是否在文本中显式指定了地点。

        说明：
        - 该函数只用于"是否应强制使用设备定位"的决策，因此必须偏保守：
          只要疑似出现了具体地点，就返回 True，避免把"北京天气"误当作当前位置。
        """

        t = str(user_text or "").strip()
        if not t:
            return False

        # 1) 明确坐标或 LocationID
        if re.search(r"-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?", t):
            return True
        if re.search(r"\b\d{6,12}\b", t):
            return True

        # 2) 类地名后缀（市/区/县/镇等）
        if re.search(r"[\u4e00-\u9fff]{1,10}(?:市|区|县|镇|乡|村|街道)", t):
            return True

        # 3) 典型模式：<疑似地点><天气/温度/预报>
        # 排除时间词（例如"今天/现在"），避免把"今天天气"误判为地点。
        excluded = {
            "今天",
            "明天",
            "后天",
            "现在",
            "今晚",
            "今夜",
            "早上",
            "上午",
            "下午",
            "傍晚",
            "晚上",
            "中午",
            "凌晨",
            "今日",
            "本周",
            "这周",
            "这几天",
            "最近",
        }
        m = re.search(r"([\u4e00-\u9fff]{2,8})(?:的)?(?:今天|现在|明天|后天)?(?:天气|气温|温度|预报)", t)
        if m:
            candidate = str(m.group(1) or "").strip()
            if candidate and candidate not in excluded and candidate not in {"这里", "我这", "我这里", "当前位置"}:
                return True

        return False

    @staticmethod
    def _is_location_request(user_text: str) -> bool:
        t = str(user_text or "").strip()
        if not t:
            return False

        keywords = [
            "定位",
            "位置",
            "我在哪",
            "我在哪里",
            "在哪儿",
            "在什么地方",
            "当前位置",
            "我现在在哪",
        ]
        return any(k in t for k in keywords)

    def _should_prefer_device_location(self, user_text: str) -> bool:
        """当用户问"天气/定位"但没指定地点时，优先使用设备定位。

        例如：
        - "今天天气怎么样？" ✅ 使用设备定位
        - "我现在的定位是在哪里？" ✅ 使用设备定位
        - "深圳今天天气怎么样？" ❌ 不强制使用设备定位（由模型按用户指定城市查询）
        """

        if not (self._is_weather_request(user_text) or self._is_location_request(user_text)):
            return False

        return not self._contains_explicit_location_hint(user_text)

    @staticmethod
    def _make_tool_call(name: str, args: Dict[str, Any], *, prefix: str) -> Dict[str, Any]:
        now = int(time.time() * 1000)
        return {
            "id": f"{prefix}_{now}_{random.randint(1000, 9999)}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        }

    @staticmethod
    def _tool_name_from_call(tool_call: Dict[str, Any]) -> Optional[str]:
        if not isinstance(tool_call, dict):
            return None
        fn = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else None
        name = fn.get("name") if isinstance(fn, dict) else tool_call.get("name")
        return name if isinstance(name, str) and name.strip() else None

    async def _maybe_run_network_tool_loop(
        self,
        *,
        sid: str,
        request_id: str,
        user_text: str,
        messages: List[Dict[str, str]],
        llm_resp: Dict[str, Any],
        tool_defs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """当且仅当 tool_calls 全部属于"联网工具"时，执行工具回填循环。

        说明：
        - 这样可以避免"工具集合里混入本地执行工具"导致的未回填 tool_call_id 问题。
        - 只有在用户开启联网开关时，本方法才会被调用。
        """

        tool_calls = llm_resp.get("toolCalls") if isinstance(llm_resp.get("toolCalls"), list) else []
        if not tool_calls:
            return llm_resp

        # 关键修复：tool-loop 必须使用“解析后的 session_id”（通常是 clientId），
        # 不能直接用原始 sid（socket sid 或 room "client:<id>"），否则会读到错误的默认会话态，
        # 表现为：capability 显示开启，但实际执行时 enabled=false（与你的 ui_debug 证据一致）。
        session_id, _emit_to = self._resolve_session_and_emit_to(sid)

        names = [self._tool_name_from_call(tc) for tc in tool_calls]
        if not names or any(n is None for n in names):
            return llm_resp

        if not all((self.network_tools_service.is_network_tool(n) or n == "get_device_location") for n in names if n):
            return llm_resp

        loop_messages: List[Dict[str, Any]] = list(messages)
        current = llm_resp

        # tool-loop 执行时可读取 session 中的设备定位（用于修复 location=auto 导致的错误地理解析）。
        session = self.session_store.get_or_create(session_id)
        device_lon_lat = None
        if bool(getattr(session, "device_location_enabled", False)) and isinstance(getattr(session, "device_location", None), dict):
            device_lon_lat = str((session.device_location or {}).get("lonLat") or "").strip() or None

        for round_idx in range(3):
            tool_calls = current.get("toolCalls") if isinstance(current.get("toolCalls"), list) else []
            if not tool_calls:
                return current

            names = [self._tool_name_from_call(tc) for tc in tool_calls]
            if not names or any(n is None for n in names):
                return current
            if not all((self.network_tools_service.is_network_tool(n) or n == "get_device_location") for n in names if n):
                return current

            # 记录本轮 tool_calls（含解析后的参数），用于定位"location=auto"等问题。
            try:
                tc_dbg = []
                for tc in tool_calls[:10]:
                    name = self._tool_name_from_call(tc) or ""
                    args = None
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else None
                    if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
                        try:
                            args = json.loads(fn.get("arguments") or "{}")
                        except Exception:
                            args = fn.get("arguments")
                    tc_dbg.append({"name": name, "args": args})

                self.network_tools_service.dump_debug_artifact(
                    request_id=request_id,
                    tag=f"net_tool_calls_round_{round_idx}",
                    payload={
                        "requestId": request_id,
                        "round": round_idx,
                        "userText": user_text,
                        "toolCalls": tc_dbg,
                    },
                )
            except Exception:
                pass

            # 把 assistant 的 tool_calls 追加到 messages（用于下一轮回填）。
            loop_messages.append(
                {
                    "role": "assistant",
                    "content": current.get("text") or "",
                    "tool_calls": tool_calls,
                }
            )

            async def _exec_device_location(tc: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
                nonlocal device_lon_lat

                tool_call_id = str(tc.get("id") or "")
                tool_name = "get_device_location"

                args_raw = (tc.get("function") or {}).get("arguments") if isinstance(tc.get("function"), dict) else None
                args_obj: Dict[str, Any] = {}
                if isinstance(args_raw, str) and args_raw.strip():
                    try:
                        args_obj = json.loads(args_raw)
                    except Exception:
                        args_obj = {}

                timeout_sec = args_obj.get("timeoutSec")
                try:
                    timeout_sec_i = int(timeout_sec) if timeout_sec is not None else self.device_location_service.DEFAULT_TIMEOUT_SEC
                except Exception:
                    timeout_sec_i = self.device_location_service.DEFAULT_TIMEOUT_SEC
                timeout_sec_i = max(3, min(30, timeout_sec_i))

                self.network_tools_service.dump_debug_artifact(
                    request_id=request_id,
                    tag="local_tool_request_get_device_location",
                    payload={
                        "requestId": request_id,
                        "round": round_idx,
                        "toolCallId": tool_call_id,
                        "timeoutSec": timeout_sec_i,
                        "enabled": bool(getattr(session, "device_location_enabled", False)),
                        "available": bool(self.device_location_service.is_available()),
                    },
                )

                if not bool(getattr(session, "device_location_enabled", False)):
                    err = "未开启设备定位，请在设置中开启'设备定位'并授予系统定位权限。"
                    self.network_tools_service.dump_debug_artifact(
                        request_id=request_id,
                        tag="local_tool_error_get_device_location",
                        payload={
                            "requestId": request_id,
                            "round": round_idx,
                            "toolCallId": tool_call_id,
                            "error": err,
                        },
                    )
                    return (
                        tool_call_id,
                        json.dumps({"error": err}, ensure_ascii=False),
                        {"ok": False, "tool": tool_name, "args": {"timeoutSec": timeout_sec_i}, "error": err},
                    )

                # 关键：CoreLocation 的授权弹窗/回调依赖主线程 RunLoop。
                # 因此这里不能丢到线程池执行，否则可能导致授权状态不刷新并最终超时。
                started_ms = int(time.time() * 1000)

                try:
                    r = self.device_location_service.get_current_location(timeout_sec=timeout_sec_i)
                except Exception as e:
                    err = f"调用 CoreLocation 失败：{e}"
                    self.network_tools_service.dump_debug_artifact(
                        request_id=request_id,
                        tag="local_tool_error_get_device_location",
                        payload={
                            "requestId": request_id,
                            "round": round_idx,
                            "toolCallId": tool_call_id,
                            "error": err,
                        },
                    )
                    return (
                        tool_call_id,
                        json.dumps({"error": err}, ensure_ascii=False),
                        {"ok": False, "tool": tool_name, "args": {"timeoutSec": timeout_sec_i}, "error": err},
                    )

                finished_ms = int(time.time() * 1000)

                if not getattr(r, "ok", False):
                    err = str(getattr(r, "error", "获取设备定位失败"))
                    self.network_tools_service.dump_debug_artifact(
                        request_id=request_id,
                        tag="local_tool_error_get_device_location",
                        payload={
                            "requestId": request_id,
                            "round": round_idx,
                            "toolCallId": tool_call_id,
                            "error": err,
                            "elapsedMs": finished_ms - started_ms,
                        },
                    )
                    return (
                        tool_call_id,
                        json.dumps({"error": err}, ensure_ascii=False),
                        {"ok": False, "tool": tool_name, "args": {"timeoutSec": timeout_sec_i}, "error": err},
                    )

                result_obj = getattr(r, "result", None) if r is not None else None
                if not isinstance(result_obj, dict) or not result_obj.get("lon_lat"):
                    err = "设备定位返回数据无效"
                    self.network_tools_service.dump_debug_artifact(
                        request_id=request_id,
                        tag="local_tool_error_get_device_location",
                        payload={
                            "requestId": request_id,
                            "round": round_idx,
                            "toolCallId": tool_call_id,
                            "error": err,
                            "result": result_obj,
                        },
                    )
                    return (
                        tool_call_id,
                        json.dumps({"error": err}, ensure_ascii=False),
                        {"ok": False, "tool": tool_name, "args": {"timeoutSec": timeout_sec_i}, "error": err},
                    )

                device_lon_lat = str(result_obj.get("lon_lat") or "").strip() or device_lon_lat

                # 基于经纬度反查城市信息（QWeather Geo lookup）。
                geo_error = None
                try:
                    lon_lat_parts = [p.strip() for p in str(device_lon_lat or "").split(",")]
                    if len(lon_lat_parts) == 2:
                        lon_f = float(lon_lat_parts[0])
                        lat_f = float(lon_lat_parts[1])
                        lon_lat_2dp = f"{lon_f:.2f},{lat_f:.2f}"

                        self.network_tools_service.dump_debug_artifact(
                            request_id=request_id,
                            tag="device_location_geo_lookup_request",
                            payload={
                                "requestId": request_id,
                                "round": round_idx,
                                "toolCallId": tool_call_id,
                                "lonLatRaw": device_lon_lat,
                                "lonLat2dp": lon_lat_2dp,
                                "range": "cn",
                                "lang": "zh",
                                "number": 10,
                            },
                        )

                        geo = await self.network_tools_service.qweather_city_lookup(
                            location=lon_lat_2dp,
                            range_="cn",
                            lang="zh",
                            number=10,
                        )

                        chosen = None
                        if isinstance(geo, dict) and str(geo.get("code") or "") == "200":
                            locs = geo.get("location")
                            if isinstance(locs, list) and locs:
                                chosen = locs[0] if isinstance(locs[0], dict) else None

                        self.network_tools_service.dump_debug_artifact(
                            request_id=request_id,
                            tag="device_location_geo_lookup_response",
                            payload={
                                "requestId": request_id,
                                "round": round_idx,
                                "toolCallId": tool_call_id,
                                "code": (geo or {}).get("code") if isinstance(geo, dict) else None,
                                "locationCount": len((geo or {}).get("location") or [])
                                if isinstance((geo or {}).get("location"), list)
                                else None,
                                "chosen": chosen,
                            },
                        )

                        if isinstance(chosen, dict):
                            addr = result_obj.get("address") if isinstance(result_obj.get("address"), dict) else {}

                            # 对齐 CLPlacemark 的常见字段名，便于统一展示。
                            adm2 = str(chosen.get("adm2") or "").strip() or None
                            adm1 = str(chosen.get("adm1") or "").strip() or None
                            country = str(chosen.get("country") or "").strip() or None

                            if not addr.get("locality"):
                                addr["locality"] = adm2 or str(chosen.get("name") or "").strip() or None
                            if not addr.get("administrativeArea"):
                                addr["administrativeArea"] = adm1
                            if not addr.get("country"):
                                addr["country"] = country

                            addr["qweatherName"] = str(chosen.get("name") or "").strip() or None
                            addr["qweatherId"] = str(chosen.get("id") or "").strip() or None
                            addr["qweatherAdm2"] = adm2
                            addr["qweatherAdm1"] = adm1
                            addr["qweatherCountry"] = country

                            result_obj["address"] = addr
                    else:
                        geo_error = "invalid_lon_lat"
                except Exception as e:
                    geo_error = f"geo_lookup_failed: {e}"

                if geo_error:
                    result_obj["geoLookupError"] = geo_error
                    try:
                        self.network_tools_service.dump_debug_artifact(
                            request_id=request_id,
                            tag="device_location_geo_lookup_error",
                            payload={
                                "requestId": request_id,
                                "round": round_idx,
                                "toolCallId": tool_call_id,
                                "error": geo_error,
                            },
                        )
                    except Exception:
                        pass

                # 将最新定位写回会话（供后续调试/兜底使用）。
                session.device_location = {
                    "lonLat": device_lon_lat,
                    "tsMs": int(result_obj.get("timestamp_ms") or finished_ms),
                    "accuracyM": result_obj.get("accuracy_m"),
                    "address": result_obj.get("address"),
                }

                self.network_tools_service.dump_debug_artifact(
                    request_id=request_id,
                    tag="local_tool_response_get_device_location",
                    payload={
                        "requestId": request_id,
                        "round": round_idx,
                        "toolCallId": tool_call_id,
                        "ok": True,
                        "elapsedMs": finished_ms - started_ms,
                        "result": result_obj,
                    },
                )

                return (tool_call_id, json.dumps(result_obj, ensure_ascii=False), {"ok": True, "tool": tool_name, "args": {"timeoutSec": timeout_sec_i}})

            async def _exec_network(tc: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
                tool_call_id = str(tc.get("id") or "")
                tool_name = self._tool_name_from_call(tc) or ""

                args_raw = (tc.get("function") or {}).get("arguments") if isinstance(tc.get("function"), dict) else None
                args_obj: Dict[str, Any] = {}
                if isinstance(args_raw, str) and args_raw.strip():
                    try:
                        args_obj = json.loads(args_raw)
                    except Exception:
                        args_obj = {}

                # 约束：当前位置/未指明城市天气不要使用公网 IP 定位。
                if tool_name == "get_ip_location":
                    raise RuntimeError("禁止使用公网 IP 定位获取当前位置，请改用 get_device_location")

                # 若存在 device lon_lat，则把 location=auto/current/here 等占位符改写为真实坐标。
                override_reason = None
                if tool_name in {"get_weather_now", "get_weather_12h"}:
                    raw_loc = args_obj.get("location")
                    raw_loc_s = str(raw_loc or "").strip().lower()
                    if raw_loc_s in {"", "auto", "current", "here", "local"}:
                        if device_lon_lat:
                            args_obj["location"] = device_lon_lat
                            override_reason = f"override_location_{raw_loc_s or 'empty'}_to_device_lon_lat"
                        else:
                            # 重要：不要 raise 未捕获异常（会导致 asyncio “Task exception was never retrieved”）。
                            # 这里应该以“工具失败”的形式返回，让上层统一收敛并提示用户开启设备定位。
                            err = "未获取到设备定位，请开启'设备定位'后重试"
                            err_payload = {
                                "tool": tool_name,
                                "ok": False,
                                "error": err,
                                "meta": {"requestId": request_id, "round": round_idx, "toolCallId": tool_call_id},
                                "requiresDeviceLocation": True,
                            }
                            self.network_tools_service.dump_debug_artifact(
                                request_id=request_id,
                                tag=f"net_tool_error_{tool_name}",
                                payload=err_payload,
                            )
                            return (
                                tool_call_id,
                                json.dumps({"error": err}, ensure_ascii=False),
                                {
                                    "ok": False,
                                    "tool": tool_name,
                                    "args": args_obj,
                                    "error": err,
                                    "requiresDeviceLocation": True,
                                },
                            )

                if override_reason:
                    self.network_tools_service.dump_debug_artifact(
                        request_id=request_id,
                        tag="location_override",
                        payload={
                            "requestId": request_id,
                            "round": round_idx,
                            "toolCallId": tool_call_id,
                            "tool": tool_name,
                            "reason": override_reason,
                            "deviceLonLat": device_lon_lat,
                        },
                    )

                req_payload = {
                    "meta": {
                        "requestId": request_id,
                        "round": round_idx,
                        "toolCallId": tool_call_id,
                        "tool": tool_name,
                    },
                    "arguments": args_obj,
                }
                self.network_tools_service.dump_debug_artifact(
                    request_id=request_id,
                    tag=f"net_tool_request_{tool_name}",
                    payload=req_payload,
                )

                try:
                    result = await self.network_tools_service.execute(
                        name=tool_name,
                        arguments=args_obj,
                        request_id=request_id,
                    )
                    self.network_tools_service.dump_debug_artifact(
                        request_id=request_id,
                        tag=f"net_tool_response_{tool_name}",
                        payload=result.debug_payload,
                    )
                    return (tool_call_id, result.content_for_model, {"ok": True, "tool": tool_name, "args": args_obj})
                except Exception as e:
                    err_payload = {
                        "tool": tool_name,
                        "ok": False,
                        "error": str(e),
                        "meta": {"requestId": request_id, "round": round_idx, "toolCallId": tool_call_id},
                    }
                    self.network_tools_service.dump_debug_artifact(
                        request_id=request_id,
                        tag=f"net_tool_error_{tool_name}",
                        payload=err_payload,
                    )
                    return (
                        tool_call_id,
                        json.dumps({"error": str(e)}, ensure_ascii=False),
                        {"ok": False, "tool": tool_name, "args": args_obj, "error": str(e)},
                    )

            # 先执行设备定位（若模型要求，或天气工具使用了占位符 location）。
            device_calls = [tc for tc in tool_calls if (self._tool_name_from_call(tc) or "") == "get_device_location"]
            # C 修复：不要只检查 “auto” 子串；location="" 也是占位符（你的 ui_debug 里就是空串）。
            needs_device_for_weather = False
            for tc in tool_calls:
                name = self._tool_name_from_call(tc) or ""
                if name not in {"get_weather_now", "get_weather_12h"}:
                    continue
                args_raw = (tc.get("function") or {}).get("arguments") if isinstance(tc.get("function"), dict) else None
                args_obj: Dict[str, Any] = {}
                if isinstance(args_raw, str) and args_raw.strip():
                    try:
                        args_obj = json.loads(args_raw)
                    except Exception:
                        args_obj = {}
                raw_loc_s = str((args_obj.get("location") or "")).strip().lower()
                if raw_loc_s in {"", "auto", "current", "here", "local"}:
                    needs_device_for_weather = True
                    break

            results: List[Tuple[str, str, Dict[str, Any]]] = []
            if device_calls:
                results.append(await _exec_device_location(device_calls[0]))
            elif needs_device_for_weather and bool(getattr(session, "device_location_enabled", False)):
                # 模型没显式调用 get_device_location，但用了 location=auto：执行层自动补齐一次设备定位。
                auto_tc = {"id": f"auto_device_loc_{int(time.time() * 1000)}", "function": {"arguments": "{}"}}
                results.append(await _exec_device_location(auto_tc))

            # 并行执行剩余联网工具（排除 get_device_location）。
            other_calls = [tc for tc in tool_calls if (self._tool_name_from_call(tc) or "") != "get_device_location"]
            if other_calls:
                results.extend(await asyncio.gather(*[_exec_network(tc) for tc in other_calls]))

            ip_city = None
            ip_region = None
            last_weather_query = None
            weather_ok = True
            any_failed = False

            for tool_call_id, content, meta in results:
                loop_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": str(content or ""),
                    }
                )

                any_failed = any_failed or (not bool(meta.get("ok")))

                tool_name = str(meta.get("tool") or "")
                if tool_name == "get_ip_location" and meta.get("ok") is True:
                    try:
                        obj = json.loads(str(content or "{}"))
                        if isinstance(obj, dict):
                            ip_city = str(obj.get("city") or "").strip() or ip_city
                            ip_region = str(obj.get("region") or "").strip() or ip_region
                    except Exception:
                        pass

                if tool_name in {"get_weather_now", "get_weather_12h"}:
                    last_weather_query = str((meta.get("args") or {}).get("location") or "").strip() or last_weather_query
                    try:
                        obj = json.loads(str(content or "{}"))
                        if isinstance(obj, dict):
                            code = str(obj.get("code") or "").strip()
                            if code and code != "200":
                                weather_ok = False
                    except Exception:
                        # 无法解析则视为失败
                        weather_ok = False

            # 兜底策略：
            # - 天气接口失败或 code!=200：允许 web_search 兜底
            # - 设备定位失败/未授权、或误用 IP 定位：不调用 web_search，让模型提示用户开启设备定位/修正工具选择
            has_device_location_failure = any(
                (str(m.get("tool") or "") == "get_device_location") and (not bool(m.get("ok")))
                for _tid, _content, m in results
            )
            has_missing_device_location = any(
                bool(m.get("requiresDeviceLocation")) and (not bool(m.get("ok"))) for _tid, _content, m in results
            )
            has_ip_location_policy_failure = any(
                (str(m.get("tool") or "") == "get_ip_location") and (not bool(m.get("ok")))
                for _tid, _content, m in results
            )

            if (any_failed or (weather_ok is False)) and (not has_device_location_failure) and (not has_missing_device_location) and (not has_ip_location_policy_failure):
                place = ""
                if last_weather_query and (not last_weather_query.isdigit()) and "," not in last_weather_query:
                    place = last_weather_query
                elif ip_city:
                    place = f"{ip_city}{ip_region or ''}".strip()
                else:
                    place = "当前所在地"

                fallback_query = f"{place} 实时天气 未来12小时 温度 天气 风 降雨 湿度"

                fallback_call = {
                    "id": f"fallback_web_search_{int(time.time() * 1000)}_{random.randint(1000, 9999)}",
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "arguments": json.dumps({"query": fallback_query, "num": 5}, ensure_ascii=False),
                    },
                }

                self.network_tools_service.dump_debug_artifact(
                    request_id=request_id,
                    tag="net_tool_fallback_web_search",
                    payload={
                        "meta": {"requestId": request_id, "round": round_idx},
                        "reason": "network_tools_failed_or_weather_non_200",
                        "userText": user_text,
                        "query": fallback_query,
                    },
                )

                loop_messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [fallback_call],
                    }
                )

                fb_id, fb_content, _fb_meta = await _exec_network(fallback_call)
                loop_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": fb_id,
                        "content": str(fb_content or ""),
                    }
                )

                loop_messages.append(
                    {
                        "role": "system",
                        "content": (
                            "你已经拿到了 web_search 工具返回的内容。请直接基于该内容回答用户问题，并给出最终结论。\n"
                            "要求：INTENT_JSON.actions 必须是空数组，不要再发起任何工具调用；SAY 必须给出完整答案。"
                        ),
                    }
                )

                # 兜底总结阶段必须禁用 tools，避免模型再次生成 web_search。
                current = await self.llm_service.invoke_llm(
                    loop_messages,
                    tools=[],
                    parallel_tool_calls=False,
                )
                return current

            current = await self.llm_service.invoke_llm(
                loop_messages,
                tools=tool_defs,
                parallel_tool_calls=True,
            )

        return current

    async def handle_text_command(self, *, sid: str, text: Any, request_id: Optional[str]) -> None:
        session_id, emit_to = self._resolve_session_and_emit_to(sid)

        effective_request_id = (
            request_id.strip()
            if isinstance(request_id, str) and request_id.strip()
            else self._gen_request_id(session_id)
        )

        session = self.session_store.get_or_create(session_id)
        session.current_request_id = effective_request_id

        self.reset_tts_stop(session_id)

        user_text = str(text or "").strip()
        if not user_text:
            await self.sio.emit(
                "assistant-message",
                {
                    "type": "error",
                    "content": "请输入文本",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                to=emit_to,
            )
            return

        self.session_store.add_message(
            session_id,
            msg_type="user",
            content=user_text,
            metadata={"requestId": effective_request_id},
        )

        history = self.session_store.get_history(session_id, 10)
        messages = [
            {"role": "user" if m["type"] == "user" else "assistant", "content": m["content"]}
            for m in history
            if m.get("type") in {"user", "assistant"}
        ]

        include_network = bool(session.network_access_enabled)
        include_device_location = bool(getattr(session, "device_location_enabled", False)) and self.device_location_service.is_available()

        # 记录本次"设备定位工具是否可用/是否已授权开启"，便于排障。
        try:
            self.network_tools_service.dump_debug_artifact(
                request_id=effective_request_id,
                tag="device_location_capability",
                payload={
                    "requestId": effective_request_id,
                    "deviceLocationEnabled": bool(getattr(session, "device_location_enabled", False)),
                    "deviceLocationToolAvailable": bool(self.device_location_service.is_available()),
                    "effectiveIncludeDeviceLocation": bool(include_device_location),
                },
            )
        except Exception:
            pass

        tool_defs = self.llm_service.build_tool_definitions(
            include_network=include_network,
            include_device_location=include_device_location,
        )

        # 注入长期记忆上下文（user_profile + rolling_summary）
        memory_context = self.memory_service.build_memory_context()

        llm_resp = await self.llm_service.invoke_llm(
            messages,
            tools=tool_defs,
            parallel_tool_calls=include_network,
            memory_context=memory_context,
        )

        # 兼容：模型可能把"工具调用意图"写在 INTENT_JSON.actions 中，而不是 tool_calls。
        # 对于 get_device_location 与联网工具，我们必须走 tool-loop 回填，再让模型总结输出，避免被 SafetyService 当作"未知工具"拦截。
        if (include_network or include_device_location) and not (
            llm_resp.get("toolCalls") if isinstance(llm_resp.get("toolCalls"), list) else []
        ):
            parsed_first = self.parse_assistant_text(llm_resp.get("text"))
            intent_first = self.merge_intent_with_tool_calls(parsed_first.get("intent"), [])
            synthesized = self.build_tool_calls_from_intent(intent_first)
            synthesized_names = [self._tool_name_from_call(tc) or "" for tc in synthesized]
            if synthesized and all(
                self.network_tools_service.is_network_tool(name) or name == "get_device_location"
                for name in synthesized_names
            ):
                # 记录本轮"从 INTENT_JSON.actions 合成"的 tool_calls（否则后续 llm_resp 可能不带 toolCalls）。
                try:
                    tc_dbg = []
                    for tc in synthesized[:10]:
                        name = self._tool_name_from_call(tc) or ""
                        args = None
                        fn = tc.get("function") if isinstance(tc.get("function"), dict) else None
                        if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
                            try:
                                args = json.loads(fn.get("arguments") or "{}")
                            except Exception:
                                args = fn.get("arguments")
                        tc_dbg.append({"name": name, "args": args})

                    self.network_tools_service.dump_debug_artifact(
                        request_id=effective_request_id,
                        tag="synthesized_tool_calls",
                        payload={
                            "requestId": effective_request_id,
                            "userText": user_text,
                            "toolCalls": tc_dbg,
                        },
                    )
                except Exception:
                    pass

                llm_resp = {**llm_resp, "toolCalls": synthesized, "text": ""}

        if include_network or include_device_location:
            llm_resp = await self._maybe_run_network_tool_loop(
                # 关键修复：传入 session_id，避免 tool-loop 读错会话态（sid/room 可能导致默认 False）。
                sid=session_id,
                request_id=effective_request_id,
                user_text=user_text,
                messages=messages,
                llm_resp=llm_resp,
                tool_defs=tool_defs,
            )

        # 记录"本次模型/工具最终是否使用了 IP 定位/使用了哪个 location 参数"，便于复盘。
        try:
            tool_calls_raw_dbg = llm_resp.get("toolCalls") if isinstance(llm_resp.get("toolCalls"), list) else []
            tc_dbg = []
            for tc in tool_calls_raw_dbg[:10]:
                name = self._tool_name_from_call(tc) or ""
                args = None
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else None
                if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except Exception:
                        args = fn.get("arguments")
                tc_dbg.append({"name": name, "args": args})

            self.network_tools_service.dump_debug_artifact(
                request_id=effective_request_id,
                tag="location_tool_calls",
                payload={
                    "requestId": effective_request_id,
                    "userText": user_text,
                    "toolCalls": tc_dbg,
                },
            )
        except Exception:
            pass

        parsed = self.parse_assistant_text(llm_resp.get("text"))
        response_text = self._sanitize_say_text(parsed.get("sayText") or "")

        tool_calls_raw = llm_resp.get("toolCalls") if isinstance(llm_resp.get("toolCalls"), list) else []
        # 重要：联网工具与 get_device_location 必须走"工具回填循环"，不能走 ToolRouter。
        tool_calls = [
            tc
            for tc in tool_calls_raw
            if not self.network_tools_service.is_network_tool(self._tool_name_from_call(tc) or "")
            and (self._tool_name_from_call(tc) or "") != "get_device_location"
        ]

        intent = self.merge_intent_with_tool_calls(parsed.get("intent"), tool_calls)

        tool_calls_from_intent = [] if tool_calls else self.build_tool_calls_from_intent(intent)
        selected_tool_calls = tool_calls or tool_calls_from_intent

        # 若模型要求设备定位但当前未启用/不可用，则直接提示用户开启设备定位，避免走 Safety/ToolRouter。
        if selected_tool_calls and any(
            (self._tool_name_from_call(tc) or "") == "get_device_location" for tc in selected_tool_calls
        ):
            if not include_device_location:
                response_text = "未开启设备定位，请在设置中开启'设备定位'并授予系统定位权限。"
                selected_tool_calls = []
                intent = {"mode": "ask", "confidence": 0.9, "actions": [], "reason": "device_location_disabled"}

                try:
                    self.network_tools_service.dump_debug_artifact(
                        request_id=effective_request_id,
                        tag="device_location_blocked",
                        payload={
                            "requestId": effective_request_id,
                            "deviceLocationEnabled": bool(getattr(session, "device_location_enabled", False)),
                            "deviceLocationToolAvailable": bool(self.device_location_service.is_available()),
                            "effectiveIncludeDeviceLocation": bool(include_device_location),
                        },
                    )
                except Exception:
                    pass

        fallback_applied = False
        fallback_reason = None

        # 兜底：当模型未输出动作时，避免出现"口头说在播放但其实没执行"的假播放。
        # 注意：后端不实现"随机选歌库"；随机化应由 LLM 生成真实的 query。
        if not selected_tool_calls and self._is_music_request(user_text):
            q = self._extract_music_search_query(user_text)
            if q:
                response_text = f"我可以用酷狗搜索并播放\u201c{q}\u201d。这需要你确认一下。"
                selected_tool_calls = [
                    self._make_tool_call(
                        "music_ui",
                        {"player": "kugou", "action": "search", "query": q},
                        prefix="fallback_music_ui",
                    )
                ]
                fallback_reason = "song_search_requires_confirmation"
            else:
                response_text = "好，我先打开酷狗开始播放。"
                selected_tool_calls = [
                    self._make_tool_call(
                        "play_music",
                        {"source": "kugou"},
                        prefix="fallback_play_music",
                    )
                ]
                fallback_reason = "generic_music_default_kugou"

            intent = self.merge_intent_with_tool_calls(intent, selected_tool_calls)
            fallback_applied = True

        # 强制升级：即便模型选了低风险 play_music，只要带明确曲目 query，也升级为 UI 搜索播放。
        forced_reason = None
        if selected_tool_calls:
            first = selected_tool_calls[0] if isinstance(selected_tool_calls, list) else None
            fn = first.get("function") if isinstance(first, dict) else None
            first_name = fn.get("name") if isinstance(fn, dict) else first.get("name") if isinstance(first, dict) else None
            args_raw = fn.get("arguments") if isinstance(fn, dict) else first.get("arguments") if isinstance(first, dict) else None

            args_obj: Dict[str, Any] = {}
            if isinstance(args_raw, str):
                try:
                    args_obj = json.loads(args_raw)
                except Exception:
                    args_obj = {}
            elif isinstance(args_raw, dict):
                args_obj = args_raw

            # 1) 若模型选择了低风险 play_music 但同时给出了 query，则升级为 UI 搜索播放。
            # 注意：这里不再硬编码解析用户原话，query 的归一化应由 LLM 完成。
            if forced_reason is None and first_name == "play_music" and self._is_music_request(user_text):
                src = str(args_obj.get("source") or "").strip().lower()
                q_from_model = str(args_obj.get("query") or "").strip()
                if src in {"", "kugou"} and q_from_model:
                    response_text = f"我可以用酷狗搜索并播放\u201c{q_from_model}\u201d。这需要你确认一下。"
                    selected_tool_calls = [
                        self._make_tool_call(
                            "music_ui",
                            {"player": "kugou", "action": "search", "query": q_from_model},
                            prefix="force_music_ui",
                        )
                    ]
                    intent = self.merge_intent_with_tool_calls(intent, selected_tool_calls)
                    forced_reason = "force_music_ui_for_song_query_from_model"

            # "我喜欢第一首"：用户明确要求播放我喜欢里的第一首
            if forced_reason is None:
                t = str(user_text or "")
                if ("我喜欢" in t or "喜欢的歌" in t or "我喜爱" in t) and ("第一首" in t or "第一首歌" in t):
                    response_text = "我可以在酷狗打开\u201c我喜欢\u201d并播放第一首。这需要你确认一下。"
                    selected_tool_calls = [
                        self._make_tool_call(
                            "music_ui",
                            {"player": "kugou", "action": "favorites_first"},
                            prefix="force_music_ui",
                        )
                    ]
                    intent = self.merge_intent_with_tool_calls(intent, selected_tool_calls)
                    forced_reason = "force_music_ui_favorites_first"

        if forced_reason:
            logger.info(
                "force tool override: requestId=%s reason=%s userText=%s",
                effective_request_id,
                forced_reason,
                user_text,
            )
            fallback_applied = True
            fallback_reason = forced_reason

        # 若仍无动作，确保不会声称"正在播放/已开始播放"。
        if not selected_tool_calls and self._looks_like_playback_claim(response_text):
            response_text = "我还没开始播放。你想听什么歌？或者直接说\u201c播放音乐\u201d。"

        if not response_text.strip() and (intent or {}).get("actions"):
            response_text = "好呀，我来处理。"

        self.session_store.add_message(
            session_id,
            msg_type="assistant",
            content=response_text,
            metadata={
                "toolCalls": llm_resp.get("toolCalls"),
                "intent": intent,
                "model": llm_resp.get("model"),
                "usage": llm_resp.get("usage"),
                "fallback": {"applied": fallback_applied, "reason": fallback_reason},
            },
        )

        # 异步触发长期记忆更新（摘要 + 偏好抽取），不阻塞主链路
        asyncio.create_task(
            self._update_memory_after_turn(
                user_text=user_text,
                assistant_text=response_text,
            )
        )

        if response_text.strip():
            await self.sio.emit(
                "assistant-message",
                {
                    "type": "text",
                    "content": response_text,
                    "requestId": effective_request_id,
                    "metadata": {"intent": intent},
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                to=emit_to,
            )

        if selected_tool_calls:
            await self.handle_tool_calls(sid=emit_to, tool_calls=selected_tool_calls)
            return

        # TTS 流式输出
        voice_settings = session.tts_settings or {"gender": "female", "rate": 1.0, "pitch": 1.0}

        cancel_event = asyncio.Event()

        async def _run_tts() -> None:
            try:
                async def on_chunk(wav_bytes: bytes) -> None:
                    if self.is_tts_stopped(session_id):
                        cancel_event.set()
                        return
                    if wav_bytes:
                        await self.sio.emit(
                            "audio-chunk",
                            {
                                "audioData": wav_bytes,
                                "text": response_text,
                                "requestId": effective_request_id,
                                "isComplete": False,
                            },
                            to=emit_to,
                        )

                audio_full = await self.tts_service.text_to_speech(
                    response_text,
                    voice_settings,
                    on_audio_chunk=on_chunk,
                    cancel_event=cancel_event,
                    request_id=effective_request_id,
                )

                # 无论是否有完整音频，都发送 completion，避免前端一直等待。
                await self.sio.emit(
                    "audio-chunk",
                    {
                        "audioData": b"",
                        "text": response_text,
                        "requestId": effective_request_id,
                        "isComplete": True,
                    },
                    to=emit_to,
                )

                if not audio_full:
                    await self.sio.emit(
                        "audio-response",
                        {
                            "audioData": b"",
                            "text": response_text,
                            "requestId": effective_request_id,
                            "settings": voice_settings,
                        },
                        to=emit_to,
                    )

            except asyncio.CancelledError:
                # 尽力而为：仍发送 completion，保证前端状态可收敛。
                await self.sio.emit(
                    "audio-chunk",
                    {
                        "audioData": b"",
                        "text": response_text,
                        "requestId": effective_request_id,
                        "isComplete": True,
                    },
                    to=emit_to,
                )
            except Exception:
                await self.sio.emit(
                    "audio-response",
                    {
                        "audioData": b"",
                        "text": response_text,
                        "requestId": effective_request_id,
                        "settings": voice_settings,
                    },
                    to=emit_to,
                )

        task = asyncio.create_task(_run_tts())
        self._tts_tasks[session_id] = task

    async def _update_memory_after_turn(
        self,
        *,
        user_text: str,
        assistant_text: str,
    ) -> None:
        """每轮对话后异步更新长期记忆（滚动摘要 + 偏好抽取）。

        不阻塞主链路，失败时仅记录警告。
        """
        if not self.memory_service.enabled:
            return

        latest_messages = [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ]

        # 1) 更新滚动摘要
        try:
            summary_msgs = self.memory_service.build_summary_update_messages(latest_messages)
            summary_resp = await self.llm_service.invoke_llm(
                summary_msgs,
                max_tokens=600,
            )
            new_summary = str(summary_resp.get("text") or "").strip()
            if new_summary:
                self.memory_service.save_rolling_summary(new_summary)
                logger.debug("滚动摘要已更新 (%d 字符)", len(new_summary))
        except Exception as e:
            logger.warning("滚动摘要更新失败: %s", e)

        # 2) 偏好抽取（仅在用户消息包含触发词时）
        if self.memory_service.should_extract_preferences(user_text):
            try:
                pref_msgs = self.memory_service.build_preference_extract_messages(user_text)
                pref_resp = await self.llm_service.invoke_llm(
                    pref_msgs,
                    max_tokens=400,
                )
                pref_text = str(pref_resp.get("text") or "").strip()
                if pref_text:
                    self.memory_service.parse_and_save_profile(pref_text)
            except Exception as e:
                logger.warning("偏好抽取失败: %s", e)

    async def handle_tool_calls(self, *, sid: str, tool_calls: List[Dict[str, Any]]) -> None:
        session_id, emit_to = self._resolve_session_and_emit_to(sid)

        # 与 Node 侧对齐：只处理前几条 tool call。
        for tc in tool_calls[:3]:
            fn = tc.get("function") or {}
            name = fn.get("name") or tc.get("name")
            args_raw = fn.get("arguments")
            args: Dict[str, Any] = {}
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw)
                except Exception:
                    args = {}

            parsed_tool_call = {"id": tc.get("id"), "name": name, "arguments": args}

            session = self.session_store.get_or_create(session_id)
            allow_local = self.is_local_control_allowed(session_id)

            # 防御性兜底：联网工具不应走 ToolRouter/SafetyService；它们必须在 handle_text_command 的 tool-loop 中执行并回填。
            if self.network_tools_service.is_network_tool(str(name or "").strip()):
                msg = "联网查询未开启，请在设置里打开\u201c允许联网查询\u201d后再试。"
                if session.network_access_enabled:
                    msg = "联网查询已开启，但本次请求未走联网工具回填链路。请重试该问题。"
                await self.sio.emit(
                    "assistant-message",
                    {
                        "type": "system",
                        "content": msg,
                        "toolCall": parsed_tool_call,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    },
                    to=emit_to,
                )
                continue

            risk = self.safety_service.validate_tool_call(parsed_tool_call, allow_local_control=allow_local)
            if not risk.allowed:
                await self.sio.emit(
                    "assistant-message",
                    {
                        "type": "safety_warning",
                        "content": f"操作被阻止：{risk.reason}",
                        "toolCall": parsed_tool_call,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    },
                    to=emit_to,
                )
                continue

            # 需要确认
            if risk.requires_confirmation:
                confirmation = self.safety_service.generate_confirmation_request(parsed_tool_call, risk)
                self.session_store.set_pending_confirmation(session_id, confirmation)
                self.session_store.set_pending_tool_call(
                    session_id,
                    parsed_tool_call,
                    {
                        "riskLevel": risk.risk_level,
                        "requiresConfirmation": True,
                        "reason": risk.reason,
                    },
                )

                await self.sio.emit("request-confirmation", confirmation, to=emit_to)
                return

            await self.execute_tool_call(sid=emit_to, tool_call=parsed_tool_call)

    async def execute_tool_call(self, *, sid: str, tool_call: Dict[str, Any]) -> None:
        session_id, emit_to = self._resolve_session_and_emit_to(sid)

        self.session_store.add_message(
            session_id,
            msg_type="tool_call",
            content=f"调用工具: {tool_call.get('name')}",
            metadata={"toolCall": tool_call},
        )

        session = self.session_store.get_or_create(session_id)
        result = await self.tool_router.route_and_execute(
            tool_call,
            {
                "socketId": emit_to,
                "session": session,
                "allowLocalControl": self.is_local_control_allowed(session_id),
            },
        )

        self.session_store.add_message(
            session_id,
            msg_type="tool_result",
            content=(result.get("result") or {}).get("message") if result.get("success") else result.get("error", ""),
            metadata={"result": result},
        )

        await self.sio.emit(
            "tool-result",
            {
                "type": "success" if result.get("success") else "error",
                "toolCall": {"id": result.get("toolCallId"), "name": result.get("name")},
                "result": result.get("result") if result.get("success") else None,
                "error": None if result.get("success") else result.get("error"),
                "timestamp": result.get("timestamp"),
            },
            to=emit_to,
        )

        # 工具执行结果的语音反馈
        if result.get("success") and (result.get("result") or {}).get("message"):
            msg = (result.get("result") or {}).get("message")
            voice_settings = session.tts_settings or {"gender": "female", "rate": 1.0, "pitch": 1.0}
            audio = await self.tts_service.text_to_speech(msg, voice_settings, request_id=session.current_request_id)
            await self.sio.emit(
                "audio-response",
                {
                    "audioData": audio,
                    "text": msg,
                    "requestId": session.current_request_id,
                    "settings": voice_settings,
                },
                to=emit_to,
            )

    async def handle_confirmation(self, *, sid: str, confirmation_id: Any, approved: Any) -> None:
        session_id, emit_to = self._resolve_session_and_emit_to(sid)

        pending = self.session_store.get_pending_confirmation(session_id)
        if not pending or pending.get("id") != confirmation_id:
            await self.sio.emit(
                "error",
                {"message": "确认请求不存在或已过期"},
                to=emit_to,
            )
            return

        pending_tool_call = self.session_store.get_or_create(session_id).pending_tool_call
        self.session_store.clear_pending_confirmation(session_id)

        if not pending_tool_call:
            await self.sio.emit("error", {"message": "待处理的工具调用不存在"}, to=emit_to)
            return

        if bool(approved) is True:
            self.session_store.clear_pending_tool_call(session_id)
            await self.execute_tool_call(sid=emit_to, tool_call=pending_tool_call)
            return

        # 用户拒绝
        self.session_store.clear_pending_tool_call(session_id)
        await self.sio.emit(
            "assistant-message",
            {
                "type": "canceled",
                "content": "好的，那我不执行这个操作。",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            to=emit_to,
        )

    async def handle_cancel(self, *, sid: str, silent: bool) -> None:
        session_id, emit_to = self._resolve_session_and_emit_to(sid)

        # 取消待确认/待执行工具，并停止 TTS
        self.stop_tts(session_id)
        self.session_store.clear_pending_confirmation(session_id)
        self.session_store.clear_pending_tool_call(session_id)
        self.session_store.mark_canceled(session_id)

        if not silent:
            await self.sio.emit(
                "assistant-message",
                {
                    "type": "canceled",
                    "content": "当前操作已取消。",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                to=emit_to,
            )

    async def handle_stop_music(self, *, sid: str) -> None:
        session_id, emit_to = self._resolve_session_and_emit_to(sid)

        if not self.is_local_control_allowed(session_id):
            await self.sio.emit(
                "assistant-message",
                {
                    "type": "system",
                    "content": "本地应用操控已禁用，已忽略停止音乐请求",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                to=emit_to,
            )
            return

        result = await self.tool_router.route_and_execute(
            {"id": f"stop_music_{int(time.time()*1000)}", "name": "stop_music", "arguments": {}},
            {"socketId": emit_to},
        )
        msg = (result.get("result") or {}).get("message") or "音乐已停止"
        await self.sio.emit(
            "assistant-message",
            {
                "type": "system",
                "content": msg,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            to=emit_to,
        )
