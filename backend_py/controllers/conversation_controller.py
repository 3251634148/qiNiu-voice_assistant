from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple

import socketio

from backend_py.safety import RiskAssessment, SafetyService
from backend_py.services.asr_service import ASRService
from backend_py.services.llm_service import LLMService
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
        self.tts_service = TTSService()
        self.asr_service = ASRService()
        self.tool_router = ToolRouter(llm_service=self.llm_service)

        self._tts_tasks: Dict[str, asyncio.Task] = {}
        self.connected_count = 0

    def get_supported_tool_names(self) -> list[str]:
        return [t["name"] for t in self.tool_router.get_supported_tools()]

    def on_connect(self, sid: str) -> None:
        self.connected_count += 1
        self.session_store.get_or_create(sid)

    def on_disconnect(self, sid: str) -> None:
        self.connected_count = max(0, self.connected_count - 1)
        self.stop_tts(sid)
        self.session_store.clear(sid)

    @staticmethod
    def _coerce_audio_bytes(audio_data: Any) -> bytes:
        if audio_data is None:
            return b""
        if isinstance(audio_data, (bytes, bytearray)):
            return bytes(audio_data)
        if isinstance(audio_data, list) and all(isinstance(x, int) for x in audio_data):
            return bytes(audio_data)
        if isinstance(audio_data, str):
            # Some clients may send base64 string
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
                to=sid,
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
                to=sid,
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
                to=sid,
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
            to=sid,
        )

        await self.handle_text_command(sid=sid, text=recognized_text, request_id=request_id)

    def _gen_request_id(self, sid: str) -> str:
        return f"req_{sid}_{int(time.time() * 1000)}_{random.randint(100000, 999999)}"

    def reset_tts_stop(self, sid: str) -> None:
        session = self.session_store.get_or_create(sid)
        session.tts_stopped = False

    def is_tts_stopped(self, sid: str) -> bool:
        session = self.session_store.get_or_create(sid)
        return session.tts_stopped is True

    def stop_tts(self, sid: str) -> None:
        session = self.session_store.get_or_create(sid)
        session.tts_stopped = True

        task = self._tts_tasks.pop(sid, None)
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
            session = self.session_store.get_or_create(sid)
            tts = {
                "gender": settings.get("gender") or "female",
                "rate": float(settings.get("rate") or 1.0),
                "pitch": float(settings.get("pitch") or 1.0),
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

    def get_tts_settings(self, sid: str) -> Dict[str, Any]:
        try:
            session = self.session_store.get_or_create(sid)
            return {
                "success": True,
                "settings": session.tts_settings
                or {"gender": "female", "rate": 1.0, "pitch": 1.0, "model": None},
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_available_voices(self) -> Dict[str, Any]:
        try:
            return {"success": True, "voices": self.tts_service.get_available_voices()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_session_status(self, sid: str) -> Dict[str, Any]:
        return self.session_store.get_status(sid)

    def get_session_history(self, sid: str, limit: int) -> List[Dict[str, Any]]:
        return self.session_store.get_history(sid, limit)

    def clear_session(self, sid: str) -> None:
        self.session_store.clear(sid)

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

    def parse_assistant_text(self, raw_text: str) -> Dict[str, Any]:
        text = str(raw_text or "")
        lines = text.splitlines()
        if not lines:
            return {"intent": None, "sayText": ""}

        first = lines[0]
        if first.startswith("INTENT_JSON:"):
            json_part = first[len("INTENT_JSON:") :].strip()
            intent = None
            try:
                intent = self.normalize_intent_object(json.loads(json_part))
            except Exception:
                intent = None

            say_lines = lines[1:]
            if say_lines and say_lines[0].startswith("SAY:"):
                say_lines[0] = say_lines[0][len("SAY:") :].lstrip()

            return {"intent": intent, "sayText": "\n".join(say_lines).strip()}

        if text.startswith("SAY:"):
            return {"intent": None, "sayText": text[len("SAY:") :].strip()}

        return {"intent": None, "sayText": text.strip()}

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
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                }
            )
        return calls

    async def handle_text_command(self, *, sid: str, text: Any, request_id: Optional[str]) -> None:
        effective_request_id = request_id.strip() if isinstance(request_id, str) and request_id.strip() else self._gen_request_id(sid)

        session = self.session_store.get_or_create(sid)
        session.current_request_id = effective_request_id

        self.reset_tts_stop(sid)

        user_text = str(text or "").strip()
        if not user_text:
            await self.sio.emit(
                "assistant-message",
                {
                    "type": "error",
                    "content": "请输入文本",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                to=sid,
            )
            return

        self.session_store.add_message(sid, msg_type="user", content=user_text, metadata={"requestId": effective_request_id})

        history = self.session_store.get_history(sid, 10)
        messages = [
            {"role": "user" if m["type"] == "user" else "assistant", "content": m["content"]}
            for m in history
            if m.get("type") in {"user", "assistant"}
        ]

        llm_resp = await self.llm_service.invoke_llm(messages)
        parsed = self.parse_assistant_text(llm_resp.get("text"))
        response_text = parsed.get("sayText") or ""
        intent = self.merge_intent_with_tool_calls(parsed.get("intent"), llm_resp.get("toolCalls"))

        if not response_text.strip() and intent.get("actions"):
            response_text = "好呀，我来处理。"

        self.session_store.add_message(
            sid,
            msg_type="assistant",
            content=response_text,
            metadata={"toolCalls": llm_resp.get("toolCalls"), "intent": intent, "model": llm_resp.get("model"), "usage": llm_resp.get("usage")},
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
                to=sid,
            )

        tool_calls = llm_resp.get("toolCalls") if isinstance(llm_resp.get("toolCalls"), list) else []
        if tool_calls:
            await self.handle_tool_calls(sid=sid, tool_calls=tool_calls)
            return

        tool_calls_from_intent = self.build_tool_calls_from_intent(intent)
        if tool_calls_from_intent:
            await self.handle_tool_calls(sid=sid, tool_calls=tool_calls_from_intent)
            return

        # TTS streaming
        voice_settings = session.tts_settings or {"gender": "female", "rate": 1.0, "pitch": 1.0}

        cancel_event = asyncio.Event()

        async def _run_tts() -> None:
            try:
                async def on_chunk(wav_bytes: bytes) -> None:
                    if self.is_tts_stopped(sid):
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
                            to=sid,
                        )

                audio_full = await self.tts_service.text_to_speech(
                    response_text,
                    voice_settings,
                    on_audio_chunk=on_chunk,
                    cancel_event=cancel_event,
                )

                # Always send completion so the frontend can stop waiting.
                await self.sio.emit(
                    "audio-chunk",
                    {
                        "audioData": b"",
                        "text": response_text,
                        "requestId": effective_request_id,
                        "isComplete": True,
                    },
                    to=sid,
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
                        to=sid,
                    )

            except asyncio.CancelledError:
                # best-effort completion
                await self.sio.emit(
                    "audio-chunk",
                    {
                        "audioData": b"",
                        "text": response_text,
                        "requestId": effective_request_id,
                        "isComplete": True,
                    },
                    to=sid,
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
                    to=sid,
                )

        task = asyncio.create_task(_run_tts())
        self._tts_tasks[sid] = task

    async def handle_tool_calls(self, *, sid: str, tool_calls: List[Dict[str, Any]]) -> None:
        # Align behavior: only handle first few calls.
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

            session = self.session_store.get_or_create(sid)
            allow_local = self.is_local_control_allowed(sid)

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
                    to=sid,
                )
                continue

            # confirmation
            if risk.requires_confirmation:
                confirmation = self.safety_service.generate_confirmation_request(parsed_tool_call, risk)
                self.session_store.set_pending_confirmation(sid, confirmation)
                self.session_store.set_pending_tool_call(sid, parsed_tool_call, {
                    "riskLevel": risk.risk_level,
                    "requiresConfirmation": True,
                    "reason": risk.reason,
                })

                await self.sio.emit("request-confirmation", confirmation, to=sid)
                return

            await self.execute_tool_call(sid=sid, tool_call=parsed_tool_call)

    async def execute_tool_call(self, *, sid: str, tool_call: Dict[str, Any]) -> None:
        self.session_store.add_message(
            sid,
            msg_type="tool_call",
            content=f"调用工具: {tool_call.get('name')}",
            metadata={"toolCall": tool_call},
        )

        session = self.session_store.get_or_create(sid)
        result = await self.tool_router.route_and_execute(tool_call, {
            "socketId": sid,
            "session": session,
            "allowLocalControl": self.is_local_control_allowed(sid),
        })

        self.session_store.add_message(
            sid,
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
            to=sid,
        )

        # voice feedback for tool result
        if result.get("success") and (result.get("result") or {}).get("message"):
            msg = (result.get("result") or {}).get("message")
            voice_settings = session.tts_settings or {"gender": "female", "rate": 1.0, "pitch": 1.0}
            audio = await self.tts_service.text_to_speech(msg, voice_settings)
            await self.sio.emit(
                "audio-response",
                {
                    "audioData": audio,
                    "text": msg,
                    "requestId": session.current_request_id,
                    "settings": voice_settings,
                },
                to=sid,
            )

    async def handle_confirmation(self, *, sid: str, confirmation_id: Any, approved: Any) -> None:
        pending = self.session_store.get_pending_confirmation(sid)
        if not pending or pending.get("id") != confirmation_id:
            await self.sio.emit(
                "error",
                {"message": "确认请求不存在或已过期"},
                to=sid,
            )
            return

        pending_tool_call = self.session_store.get_or_create(sid).pending_tool_call
        self.session_store.clear_pending_confirmation(sid)

        if not pending_tool_call:
            await self.sio.emit("error", {"message": "待处理的工具调用不存在"}, to=sid)
            return

        if bool(approved) is True:
            self.session_store.clear_pending_tool_call(sid)
            await self.execute_tool_call(sid=sid, tool_call=pending_tool_call)
            return

        # rejected
        self.session_store.clear_pending_tool_call(sid)
        await self.sio.emit(
            "assistant-message",
            {
                "type": "canceled",
                "content": "好的，那我不执行这个操作。",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            to=sid,
        )

    async def handle_cancel(self, *, sid: str, silent: bool) -> None:
        # cancel pending tool/confirmation + stop tts
        self.stop_tts(sid)
        self.session_store.clear_pending_confirmation(sid)
        self.session_store.clear_pending_tool_call(sid)
        self.session_store.mark_canceled(sid)

        if not silent:
            await self.sio.emit(
                "assistant-message",
                {
                    "type": "canceled",
                    "content": "当前操作已取消。",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                to=sid,
            )

    async def handle_stop_music(self, *, sid: str) -> None:
        if not self.is_local_control_allowed(sid):
            await self.sio.emit(
                "assistant-message",
                {
                    "type": "system",
                    "content": "本地应用操控已禁用，已忽略停止音乐请求",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                to=sid,
            )
            return

        result = await self.tool_router.route_and_execute({"id": f"stop_music_{int(time.time()*1000)}", "name": "stop_music", "arguments": {}}, {"socketId": sid})
        msg = (result.get("result") or {}).get("message") or "音乐已停止"
        await self.sio.emit(
            "assistant-message",
            {
                "type": "system",
                "content": msg,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            to=sid,
        )
