import os
from pathlib import Path

from dotenv import load_dotenv


# Align with Node backend behavior: it loads env from backend/.env.
# We load both repository root `.env` and `backend/.env` if present.
load_dotenv(dotenv_path=Path(".env"), override=False)
load_dotenv(dotenv_path=Path("backend") / ".env", override=False)


class Settings:
    """Runtime settings loaded from environment variables."""

    def __init__(self) -> None:
        self.port = int(os.getenv("PORT", "3001"))
        self.dashscope_api_key = os.getenv("DASHSCOPE_API_KEY", "")

        # WeCom (企业微信) app message
        self.wecom_corp_id = os.getenv("WECOM_CORP_ID", "")
        self.wecom_corp_secret = os.getenv("WECOM_CORP_SECRET", "")
        try:
            self.wecom_agent_id = int(os.getenv("WECOM_AGENT_ID", "0") or 0)
        except Exception:
            self.wecom_agent_id = 0


settings = Settings()
