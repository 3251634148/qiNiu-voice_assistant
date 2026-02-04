import logging
import sys
from typing import Any


def setup_logging(level: int = logging.INFO) -> None:
    """配置基础日志输出格式（结构化风格）。

    Node 后端有统一的 logger 模块；Python 侧保持简单，但输出格式尽量一致，便于排障。
    """

    handler = logging.StreamHandler(sys.stdout)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = []
    root.addHandler(handler)
    root.setLevel(level)

    # 降低第三方库噪声日志
    logging.getLogger("uvicorn").setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)


def log_extra(**kwargs: Any) -> str:
    """将额外字段渲染为紧凑的 key=value 字符串（用于日志附加信息）。"""

    parts = []
    for k, v in kwargs.items():
        parts.append(f"{k}={v!r}")
    return " ".join(parts)
