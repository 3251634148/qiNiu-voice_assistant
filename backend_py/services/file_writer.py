from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


class FileWriter:
    def __init__(self) -> None:
        self.base_dir = Path.home() / "Documents" / "VoiceAssistant"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def save_draft(self, content: str, filename: str) -> Dict[str, Any]:
        path = self.base_dir / filename
        path.write_text(content, encoding="utf-8")
        return {"filePath": str(path)}

    async def write_file(self, path: str, content: str, mode: str = "overwrite") -> Dict[str, Any]:
        p = Path(path)
        if not p.is_absolute():
            p = Path.home() / p

        if mode == "append":
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(content)
        elif mode == "create":
            if p.exists():
                raise RuntimeError("文件已存在")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

        return {"filePath": str(p)}
