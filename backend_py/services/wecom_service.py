from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from backend_py.config import settings


@dataclass
class WeComToken:
    access_token: str
    expires_at: float


class WeComService:
    """Enterprise WeChat (WeCom/企业微信) API client.

    This service is intended for *stable* automation tasks. It uses the official API:
    - gettoken
    - message/send (app message)
    - appchat/send (group chat by chatid)

    Required env:
    - WECOM_CORP_ID
    - WECOM_CORP_SECRET
    - WECOM_AGENT_ID
    """

    def __init__(self) -> None:
        self.base_url = "https://qyapi.weixin.qq.com"
        self.corp_id = settings.wecom_corp_id
        self.corp_secret = settings.wecom_corp_secret
        self.agent_id = settings.wecom_agent_id

        self._token: Optional[WeComToken] = None

    def _check_config(self) -> None:
        missing = []
        if not self.corp_id:
            missing.append("WECOM_CORP_ID")
        if not self.corp_secret:
            missing.append("WECOM_CORP_SECRET")
        if not self.agent_id:
            missing.append("WECOM_AGENT_ID")
        if missing:
            raise RuntimeError(f"企业微信配置缺失：{', '.join(missing)}")

    async def _get_access_token(self) -> str:
        self._check_config()

        now = time.time()
        if self._token and self._token.expires_at > now:
            return self._token.access_token

        async with httpx.AsyncClient(base_url=self.base_url, timeout=30.0) as client:
            resp = await client.get(
                "/cgi-bin/gettoken",
                params={
                    "corpid": self.corp_id,
                    "corpsecret": self.corp_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        errcode = int(data.get("errcode") or 0)
        if errcode != 0:
            raise RuntimeError(f"企业微信 gettoken 失败：{data}")

        token = str(data.get("access_token") or "").strip()
        expires_in = int(data.get("expires_in") or 7200)
        if not token:
            raise RuntimeError("企业微信 gettoken 未返回 access_token")

        # refresh 60s earlier
        self._token = WeComToken(access_token=token, expires_at=now + max(60, expires_in - 60))
        return token

    async def send_text(
        self,
        *,
        target_type: str,
        target: str,
        content: str,
    ) -> Dict[str, Any]:
        """Send a text message.

        Args:
            target_type: user | party | tag | chat
            target: the actual id
            content: message content

        Returns:
            Dict with wecom response fields.
        """

        token = await self._get_access_token()

        if target_type == "chat":
            payload: Dict[str, Any] = {
                "chatid": target,
                "msgtype": "text",
                "text": {"content": content},
            }
            endpoint = "/cgi-bin/appchat/send"
        else:
            payload = {
                "msgtype": "text",
                "agentid": self.agent_id,
                "text": {"content": content},
                "safe": 0,
                "enable_duplicate_check": 1,
                "duplicate_check_interval": 180,
            }
            if target_type == "party":
                payload["toparty"] = target
            elif target_type == "tag":
                payload["totag"] = target
            else:
                payload["touser"] = target

            endpoint = "/cgi-bin/message/send"

        async with httpx.AsyncClient(base_url=self.base_url, timeout=30.0) as client:
            resp = await client.post(endpoint, params={"access_token": token}, json=payload)
            resp.raise_for_status()
            data = resp.json()

        errcode = int(data.get("errcode") or 0)
        if errcode != 0:
            raise RuntimeError(f"企业微信发送失败：{data}")

        return {
            "endpoint": endpoint,
            "targetType": target_type,
            "target": target,
            "raw": data,
        }
