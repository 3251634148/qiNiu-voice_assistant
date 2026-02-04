import os
from pathlib import Path

from dotenv import load_dotenv


# 与 Node 后端行为对齐：Node 会从 backend/.env 加载环境变量。
# Python 侧同时尝试加载仓库根目录 `.env` 与 `backend/.env`（如果存在）。
load_dotenv(dotenv_path=Path(".env"), override=False)
load_dotenv(dotenv_path=Path("backend") / ".env", override=False)


class Settings:
    """运行时配置（从环境变量加载）。"""

    def __init__(self) -> None:
        self.port = int(os.getenv("PORT", "3001"))
        self.dashscope_api_key = os.getenv("DASHSCOPE_API_KEY", "")

        # WeCom（企业微信）应用消息相关配置
        self.wecom_corp_id = os.getenv("WECOM_CORP_ID", "")
        self.wecom_corp_secret = os.getenv("WECOM_CORP_SECRET", "")
        try:
            self.wecom_agent_id = int(os.getenv("WECOM_AGENT_ID", "0") or 0)
        except Exception:
            self.wecom_agent_id = 0


settings = Settings()
