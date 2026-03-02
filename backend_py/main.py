import asyncio
import logging
import time
from typing import Any, Dict, Optional

import socketio
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from backend_py.config import settings
from backend_py.controllers.conversation_controller import ConversationController
from backend_py.logging_setup import log_extra, setup_logging
from backend_py.services.hardware_voice_service import HardwareVoiceService
from backend_py.services.hotkey_voice_service import HotkeyVoiceService


setup_logging()
logger = logging.getLogger("backend_py")


sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    ping_timeout=60,
    ping_interval=25,
    allow_upgrades=True,
)

controller = ConversationController(sio=sio)

# 全局热键唤醒语音接收
_hotkey_service: Optional[HotkeyVoiceService] = None

# ESP32 硬件语音模块 WebSocket 服务
_hardware_voice_service: Optional[HardwareVoiceService] = None
if settings.hardware_ws_enabled:
    _hardware_voice_service = HardwareVoiceService()
    _hardware_voice_service.set_controller(controller)

# connection limiter: ip -> last_ts
_connection_limiter: Dict[str, float] = {}


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


@app.websocket("/ws/hardware")
async def hardware_ws_endpoint(websocket: WebSocket) -> None:
    """ESP32 硬件语音模块 WebSocket 端点。"""
    if _hardware_voice_service is None:
        await websocket.close(code=1008, reason="硬件 WebSocket 未启用")
        return
    await _hardware_voice_service.handle_websocket(websocket)


@app.get("/api/capabilities")
async def capabilities() -> Dict[str, Any]:
    return {
        "voiceRecognition": True,
        "textToSpeech": True,
        "systemControl": True,
        "fileOperations": True,
        "applicationControl": True,
        "musicControl": True,
        "safetyValidation": True,
        "sessionManagement": True,
        "toolExecution": True,
        "hotkeyVoice": settings.hotkey_enabled,
        "hardwareWebSocket": settings.hardware_ws_enabled,
        "hardwareDevices": _hardware_voice_service.connected_count if _hardware_voice_service else 0,
        "supportedTools": controller.get_supported_tool_names(),
    }


@app.get("/api/stats")
async def stats() -> Dict[str, Any]:
    data = controller.get_stats()
    data["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    data["connectedClients"] = controller.connected_count
    return data


@sio.event
async def connect(sid: str, environ: Dict[str, Any], auth: Optional[Dict[str, Any]]) -> bool:
    raw_ip = environ.get("REMOTE_ADDR") or environ.get("HTTP_X_FORWARDED_FOR") or "unknown"
    client_ip = str(raw_ip).split(",")[0].strip() if raw_ip else "unknown"
    now = time.time()

    # NOTE: 本机开发 / Electron 本地客户端在握手、升级、重连时可能出现短时间多次连接。
    # 为避免误伤，localhost 不启用连接限流。
    is_local = client_ip in {"127.0.0.1", "::1", "localhost"} or client_ip.startswith("127.")

    if not is_local:
        last = _connection_limiter.get(client_ip)
        if last and (now - last) < 1.0:
            logger.warning("拒绝频繁连接 %s", log_extra(ip=client_ip, sid=sid))
            return False
        _connection_limiter[client_ip] = now

    controller.on_connect(sid)

    logger.info("客户端连接 %s", log_extra(ip=client_ip, sid=sid))
    return True


@sio.event
async def disconnect(sid: str) -> None:
    logger.info("客户端断开连接 %s", log_extra(sid=sid))
    controller.on_disconnect(sid)


@sio.on("voice-input")
async def voice_input(sid: str, data: Dict[str, Any]) -> None:
    audio_data = (data or {}).get("audioData")
    language = (data or {}).get("language") or "zh-CN"
    request_id = (data or {}).get("requestId")
    await controller.handle_voice_input(
        sid=sid,
        audio_data=audio_data,
        language=language,
        request_id=request_id,
    )


@sio.on("register-client")
async def register_client(sid: str, data: Dict[str, Any]) -> None:
    client_id = str((data or {}).get("clientId") or "").strip()
    if not client_id:
        await sio.emit("client-registered", {"success": False, "error": "clientId 不能为空"}, to=sid)
        return

    room = f"client:{client_id}"
    await sio.enter_room(sid, room)
    controller.register_client(sid, client_id=client_id)

    logger.info("client 注册成功 %s", log_extra(sid=sid, clientId=client_id, room=room))

    await sio.emit("client-registered", {"success": True, "clientId": client_id, "room": room}, to=sid)

    # 发送一个 room ping，便于确认 room 投递链路可达（仅用于观测）。
    await sio.emit("client-room-ping", {"clientId": client_id, "room": room, "ts": int(time.time() * 1000)}, to=room)


@sio.on("text-command")
async def text_command(sid: str, data: Dict[str, Any]) -> None:
    text = (data or {}).get("text")
    request_id = (data or {}).get("requestId")
    await controller.handle_text_command(sid=sid, text=text, request_id=request_id)


@sio.on("confirm-action")
async def confirm_action(sid: str, data: Dict[str, Any]) -> None:
    confirmation_id = (data or {}).get("confirmationId")
    approved = (data or {}).get("approved")
    await controller.handle_confirmation(sid=sid, confirmation_id=confirmation_id, approved=approved)


@sio.on("cancel")
async def cancel(sid: str, data: Dict[str, Any]) -> None:
    silent = bool((data or {}).get("silent"))
    await controller.handle_cancel(sid=sid, silent=silent)


@sio.on("stop-tts")
async def stop_tts(sid: str) -> None:
    controller.stop_tts(sid=sid)


@sio.on("stop-music")
async def stop_music(sid: str) -> None:
    await controller.handle_stop_music(sid=sid)


@sio.on("get-session-status")
async def get_session_status(sid: str) -> None:
    await sio.emit("session-status", controller.get_session_status(sid), to=sid)


@sio.on("get-session-history")
async def get_session_history(sid: str, data: Dict[str, Any]) -> None:
    limit = int((data or {}).get("limit") or 50)
    await sio.emit("session-history", controller.get_session_history(sid, limit), to=sid)


@sio.on("clear-session")
async def clear_session(sid: str) -> None:
    controller.clear_session(sid)
    await sio.emit("session-history", [], to=sid)
    await sio.emit("session-status", controller.get_session_status(sid), to=sid)


@sio.on("get-system-info")
async def get_system_info(sid: str) -> None:
    info = await controller.get_system_info()
    await sio.emit("system-info", info, to=sid)


@sio.on("update-tts-settings")
async def update_tts_settings(sid: str, data: Dict[str, Any]) -> None:
    result = controller.update_tts_settings(sid, data or {})
    await sio.emit("tts-settings-updated", result, to=sid)


@sio.on("update-network-settings")
async def update_network_settings(sid: str, data: Dict[str, Any]) -> None:
    result = controller.update_network_settings(sid, data or {})
    await sio.emit("network-settings-updated", result, to=sid)


@sio.on("update-device-location")
async def update_device_location(sid: str, data: Dict[str, Any]) -> None:
    result = controller.update_device_location(sid, data or {})
    await sio.emit("device-location-updated", result, to=sid)


@sio.on("get-tts-settings")
async def get_tts_settings(sid: str) -> None:
    result = controller.get_tts_settings(sid)
    await sio.emit("tts-settings", result, to=sid)


@sio.on("get-available-voices")
async def get_available_voices(sid: str) -> None:
    result = controller.get_available_voices()
    await sio.emit("available-voices", result, to=sid)


@app.on_event("startup")
async def on_startup() -> None:
    """应用启动时初始化全局热键服务。"""
    global _hotkey_service  # noqa: PLW0603
    if settings.hotkey_enabled:
        try:
            loop = asyncio.get_running_loop()
            _hotkey_service = HotkeyVoiceService(loop=loop)
            _hotkey_service.set_controller(controller)
            _hotkey_service.start()
        except Exception as e:
            logger.warning("全局热键服务启动失败: %s", e)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """应用关闭时停止全局热键服务。"""
    if _hotkey_service is not None:
        try:
            _hotkey_service.stop()
        except Exception:
            pass


asgi_app = socketio.ASGIApp(sio, other_asgi_app=app)


def run_dev() -> None:
    import uvicorn

    uvicorn.run("backend_py.main:asgi_app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    run_dev()
