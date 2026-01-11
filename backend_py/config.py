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


settings = Settings()
