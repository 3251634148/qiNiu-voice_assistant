from __future__ import annotations

import platform
import subprocess
from typing import Any, Dict


class SystemController:
    async def get_system_info(self) -> Dict[str, Any]:
        return {
            "platform": platform.system(),
            "platformRelease": platform.release(),
            "platformVersion": platform.version(),
            "machine": platform.machine(),
            "pythonVersion": platform.python_version(),
        }

    async def open_application(self, name: str) -> Dict[str, Any]:
        system = platform.system().lower()
        if system == "darwin":
            cmd = ["open", "-a", name]
        elif system == "windows":
            cmd = ["cmd", "/c", "start", "", name]
        else:
            cmd = ["xdg-open", name]

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "打开应用失败")

        return {"command": cmd, "returncode": proc.returncode}
