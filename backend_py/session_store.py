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
    # 最近一次“显式设置联网开关”的时间戳（ms）。用于 sid->clientId 迁移合并时判定新旧优先级。
    network_access_set_at_ms: int = 0
    device_location_enabled: bool = False
    # 最近一次“显式设置设备定位开关”的时间戳（ms）。用于 sid->clientId 迁移合并时判定新旧优先级。
    device_location_set_at_ms: int = 0
    device_location: Optional[Dict[str, Any]] = None
    tts_stopped: bool = False
    current_request_id: Optional[str] = None


class SessionStore:
    """内存会话存储（与 backend/utils/sessionStore.js 行为对齐）。"""

    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}
        self.session_timeout = timedelta(hours=2)
        self.confirmation_timeout = timedelta(minutes=5)

    @staticmethod
    def _merge_session_state(*, dst: Session, src: Session) -> None:
        """将 src 的“会话态”合并进 dst（最小化覆盖）。

        设计原则（用于修复 sid->clientId 迁移的时序竞争）：
        - 不再在 dst 已存在时直接丢弃 src（会导致开关/音色等丢失）
        - 只合并“稳定配置/权限/历史”等字段，避免覆盖高风险的 pending 状态
        - 对布尔开关采用“显式设置时间戳优先”（既能保留开启，也能正确传播关闭）
        - 对 Optional 字段采用“dst 缺省时用 src”（防止误覆盖已有配置）
        """

        # 1) 权限/开关：按“最近一次显式设置”的时间戳合并（支持开启与关闭都能正确传播）。
        dst_net_ts = int(getattr(dst, "network_access_set_at_ms", 0) or 0)
        src_net_ts = int(getattr(src, "network_access_set_at_ms", 0) or 0)
        if src_net_ts > dst_net_ts:
            dst.network_access_enabled = bool(src.network_access_enabled)
            dst.network_access_set_at_ms = src_net_ts

        dst_loc_ts = int(getattr(dst, "device_location_set_at_ms", 0) or 0)
        src_loc_ts = int(getattr(src, "device_location_set_at_ms", 0) or 0)
        if src_loc_ts > dst_loc_ts:
            dst.device_location_enabled = bool(src.device_location_enabled)
            dst.device_location_set_at_ms = src_loc_ts

        # 2) 设备定位缓存（仅当 dst 缺失时使用 src）
        if dst.device_location is None and isinstance(src.device_location, dict):
            dst.device_location = src.device_location
        # 若 src 的设备定位“显式设置”更新更晚，则允许覆盖（例如用户关闭定位时需要清空缓存）
        if src_loc_ts > dst_loc_ts:
            dst.device_location = src.device_location if isinstance(src.device_location, dict) else None

        # 3) allow_local_control（仅当 dst 未显式设置时使用 src）
        if dst.allow_local_control is None and isinstance(src.allow_local_control, bool):
            dst.allow_local_control = src.allow_local_control

        # 4) tts_settings（对缺失键做补齐，不覆盖已有键）
        if isinstance(src.tts_settings, dict) and src.tts_settings:
            if not isinstance(dst.tts_settings, dict):
                dst.tts_settings = {}
            for k, v in src.tts_settings.items():
                if k not in dst.tts_settings:
                    dst.tts_settings[k] = v
                    continue

                existing = dst.tts_settings.get(k)
                is_empty_dict = isinstance(existing, dict) and (not existing)
                if existing is None or existing == "" or is_empty_dict:
                    dst.tts_settings[k] = v

        # 5) history（合并并截断到最后 100 条）
        if isinstance(src.history, list) and src.history:
            if not isinstance(dst.history, list):
                dst.history = []
            dst.history.extend([x for x in src.history if isinstance(x, dict)])
            if len(dst.history) > 100:
                dst.history = dst.history[-100:]

        # 6) requestId（仅当 dst 缺失时使用 src）
        if (dst.current_request_id is None) and isinstance(src.current_request_id, str) and src.current_request_id.strip():
            dst.current_request_id = src.current_request_id

        # 7) 生命周期字段：created_at 取更早的，last_activity 取更晚的
        try:
            dst.created_at = min(dst.created_at, src.created_at)
        except Exception:
            pass
        try:
            dst.last_activity = max(dst.last_activity, src.last_activity)
        except Exception:
            pass

    def migrate(self, from_id: str, to_id: str) -> None:
        """将会话从一个 id 迁移到另一个 id。

        用于 socket 连接 sid 与稳定 clientId 绑定后，把 session 从 sid 迁移到 clientId。

        规则：
        - 若 from_id 不存在：不做任何事
        - 若 to_id 已存在：合并 src->dst 的会话态后删除 from_id（避免因时序竞争导致开关/音色丢失）
        """
        if not from_id or not to_id or from_id == to_id:
            return

        src = self._sessions.get(from_id)
        if not src:
            return

        if to_id in self._sessions:
            dst = self._sessions.get(to_id)
            if dst is not None:
                self._merge_session_state(dst=dst, src=src)
                dst.session_id = to_id
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
