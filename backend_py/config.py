import os
from pathlib import Path

from dotenv import load_dotenv


# 与 Node 后端行为对齐：Node 会从 backend/.env 加载环境变量。
# 注意：不能依赖当前工作目录（cwd），否则在 `cd backend_py` 运行脚本时会加载失败。
REPO_ROOT = Path(__file__).resolve().parents[1]

# Python 侧优先加载仓库根目录 `.env` 与 `backend/.env`（如果存在）。
load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)
load_dotenv(dotenv_path=REPO_ROOT / "backend" / ".env", override=False)


class Settings:
    """运行时配置（从环境变量加载）。"""

    def __init__(self) -> None:
        self.port = int(os.getenv("PORT", "3002"))
        self.dashscope_api_key = os.getenv("DASHSCOPE_API_KEY", "")

        # 联网信息能力（MCP Client 工具）
        # 注意：密钥必须只从环境变量读取，严禁写入日志/落盘。
        self.serpapi_api_key = os.getenv("SERPAPI_API_KEY", "")
        self.newsdata_api_key = os.getenv("NEWSDATA_API_KEY", "")

        # QWeather
        # - 旧版：QWEATHER_API_KEY + Authorization: Bearer <key>
        # - 新版：EdDSA(JWT) + Authorization: Bearer <jwt>
        self.qweather_api_key = os.getenv("QWEATHER_API_KEY", "")
        self.qweather_api_host = os.getenv("QWEATHER_API_HOST", "")

        # QWeather JWT（Ed25519 / EdDSA）
        # - sub: 项目 ID
        # - kid: 凭据 ID
        self.qweather_jwt_sub = os.getenv("QWEATHER_JWT_SUB", "")
        self.qweather_jwt_kid = os.getenv("QWEATHER_JWT_KID", "")
        self.qweather_jwt_private_key_path = os.getenv("QWEATHER_JWT_PRIVATE_KEY_PATH", "")
        try:
            self.qweather_jwt_ttl_seconds = int(os.getenv("QWEATHER_JWT_TTL_SECONDS", "3600") or 3600)
        except Exception:
            self.qweather_jwt_ttl_seconds = 3600

        # WeCom（企业微信）应用消息相关配置
        self.wecom_corp_id = os.getenv("WECOM_CORP_ID", "")
        self.wecom_corp_secret = os.getenv("WECOM_CORP_SECRET", "")
        try:
            self.wecom_agent_id = int(os.getenv("WECOM_AGENT_ID", "0") or 0)
        except Exception:
            self.wecom_agent_id = 0


settings = Settings()
