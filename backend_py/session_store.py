from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


@dataclass
class Session:
    session_id: str
    history: List[Dict[str, Any]] = field(default_factory=list)
    pending_tool_call: Optional[Dict[str, Any]] = None
    pending_confirmation: Optional[Dict[str, Any]] = None
    canceled: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)
    context: Dict[str, Any] = field(default_factory=dict)

    # 运行时标志位
    tts_settings: Dict[str, Any] = field(default_factory=dict)
    allow_local_control: Optional[bool] = None
    network_access_enabled: bool = False
    device_location_enabled: bool = False
    device_location: Optional[Dict[str, Any]] = None
    tts_stopped: bool = False
    current_request_id: Optional[str] = None


class SessionStore:
    """内存会话存储（与 backend/utils/sessionStore.js 行为对齐）。"""

    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}
        self.session_timeout = timedelta(hours=2)
        self.confirmation_timeout = timedelta(minutes=5)

    def migrate(self, from_id: str, to_id: str) -> None:
        """将会话从一个 id 迁移到另一个 id。

        用于 socket 连接 sid 与稳定 clientId 绑定后，把 session 从 sid 迁移到 clientId。

        规则：
        - 若 from_id 不存在：不做任何事
        - 若 to_id 已存在：保留 to_id，会丢弃 from_id（避免覆盖已存在会话）
        """
        if not from_id or not to_id or from_id == to_id:
            return

        src = self._sessions.get(from_id)
        if not src:
            return

        if to_id in self._sessions:
            self._sessions.pop(from_id, None)
            return

        self._sessions[to_id] = src
        self._sessions.pop(from_id, None)
        src.session_id = to_id
        src.last_activity = datetime.utcnow()

    def get_or_create(self, sid: str) -> Session:
        if sid not in self._sessions:
            self._sessions[sid] = Session(session_id=sid)
        session = self._sessions[sid]
        session.last_activity = datetime.utcnow()
        return session

    def clear(self, sid: str) -> None:
        self._sessions.pop(sid, None)

    def add_message(self, sid: str, *, msg_type: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        session = self.get_or_create(sid)
        record = {
            "id": f"msg_{int(time.time() * 1000)}_{sid}",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "type": msg_type,
            "content": content,
            "metadata": metadata or {},
        }
        session.history.append(record)
        if len(session.history) > 100:
            session.history = session.history[-100:]
        return record

    def get_history(self, sid: str, limit: int = 50) -> List[Dict[str, Any]]:
        session = self._sessions.get(sid)
        if not session:
            return []
        return session.history[-limit:]

    def set_pending_tool_call(self, sid: str, tool_call: Dict[str, Any], risk_assessment: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        session = self.get_or_create(sid)
        pending = {
            **tool_call,
            "riskAssessment": risk_assessment,
            "createdAt": datetime.utcnow().isoformat() + "Z",
            "expiresAt": (datetime.utcnow() + self.confirmation_timeout).isoformat() + "Z",
        }
        session.pending_tool_call = pending
        session.canceled = False
        return pending

    def clear_pending_tool_call(self, sid: str) -> Optional[Dict[str, Any]]:
        session = self._sessions.get(sid)
        if not session:
            return None
        pending = session.pending_tool_call
        session.pending_tool_call = None
        session.canceled = False
        return pending

    def mark_canceled(self, sid: str, reason: str = "user_canceled") -> None:
        session = self.get_or_create(sid)
        session.canceled = True
        session.context["cancelReason"] = reason
        session.context["canceledAt"] = datetime.utcnow().isoformat() + "Z"

    def set_pending_confirmation(self, sid: str, confirmation: Dict[str, Any]) -> Dict[str, Any]:
        session = self.get_or_create(sid)
        session.pending_confirmation = {
            **confirmation,
            "createdAt": datetime.utcnow().isoformat() + "Z",
            "expiresAt": (datetime.utcnow() + self.confirmation_timeout).isoformat() + "Z",
        }
        return session.pending_confirmation

    def get_pending_confirmation(self, sid: str) -> Optional[Dict[str, Any]]:
        session = self._sessions.get(sid)
        if not session or not session.pending_confirmation:
            return None
        expires_at = session.pending_confirmation.get("expiresAt")
        if expires_at:
            try:
                exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if datetime.utcnow().replace(tzinfo=exp.tzinfo) > exp:
                    session.pending_confirmation = None
                    return None
            except Exception:
                # 若解析失败，为避免误伤用户，这里不阻断请求。
                pass
        return session.pending_confirmation

    def clear_pending_confirmation(self, sid: str) -> Optional[Dict[str, Any]]:
        session = self._sessions.get(sid)
        if not session:
            return None
        pending = session.pending_confirmation
        session.pending_confirmation = None
        return pending

    def get_status(self, sid: str) -> Dict[str, Any]:
        session = self._sessions.get(sid)
        if not session:
            return {"exists": False, "active": False}

        now = datetime.utcnow()
        is_expired = (now - session.last_activity) > self.session_timeout
        return {
            "exists": True,
            "active": not is_expired,
            "sessionId": session.session_id,
            "hasPendingToolCall": bool(session.pending_tool_call),
            "hasPendingConfirmation": bool(session.pending_confirmation),
            "isCanceled": bool(session.canceled),
            "messageCount": len(session.history),
            "lastActivity": session.last_activity.isoformat() + "Z",
            "createdAt": session.created_at.isoformat() + "Z",
        }

    def get_stats(self) -> Dict[str, Any]:
        return {
            "totalSessions": len(self._sessions),
            "activeSessions": len(self._sessions),
        }
