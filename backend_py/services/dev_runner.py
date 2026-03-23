from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


class DevRunner:
    """执行本地命令（带基础安全校验与超时控制）。

    主要用于稳定的开发流程，例如运行测试。
    为降低注入风险，这里默认不使用 `shell=True`。
    """

    def __init__(self) -> None:
        self.forbidden_tokens = {
            "rm",
            "sudo",
            "shutdown",
            "reboot",
            "mkfs",
            "dd",
            "killall",
        }

    @staticmethod
    def _tail(text: str, *, limit: int = 4000) -> str:
        if len(text) <= limit:
            return text
        return text[-limit:]

    def _validate_command(self, command: str) -> List[str]:
        raw = str(command or "").strip()
        if not raw:
            raise RuntimeError("command 不能为空")

        # 暂时禁止使用 shell 链式/注入风格符号。
        if any(x in raw for x in ["&&", ";", "|", "`", "$("]):
            raise RuntimeError("命令包含不安全的连接符号（&&/;/|/`/$()），请使用单条命令")

        argv = shlex.split(raw)
        if not argv:
            raise RuntimeError("command 无法解析")

        first = argv[0].lower()
        if first in self.forbidden_tokens:
            raise RuntimeError(f"禁止执行该命令：{first}")

        return argv

    async def run(
        self,
        *,
        cwd: str,
        command: str,
        timeout_sec: float = 120.0,
        env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        argv = self._validate_command(command)

        start = asyncio.get_event_loop().time()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            proc.kill()
            stdout_b, stderr_b = await proc.communicate()
            raise RuntimeError("命令执行超时")

        end = asyncio.get_event_loop().time()

        stdout = (stdout_b or b"").decode("utf-8", errors="ignore")
        stderr = (stderr_b or b"").decode("utf-8", errors="ignore")

        return {
            "exitCode": int(proc.returncode or 0),
            "stdoutTail": self._tail(stdout),
            "stderrTail": self._tail(stderr),
            "durationMs": int((end - start) * 1000),
            "argv": argv,
            "cwd": cwd,
        }
