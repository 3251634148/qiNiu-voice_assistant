from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List


class FileManager:
    """基础文件操作封装（供 `file_control` 工具使用）。"""

    @staticmethod
    def _resolve(path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else (Path.home() / p)

    async def list_dir(self, path: str) -> Dict[str, Any]:
        p = self._resolve(path)
        if not p.exists():
            raise RuntimeError("路径不存在")
        if not p.is_dir():
            raise RuntimeError("目标不是目录")

        items: List[Dict[str, Any]] = []
        for child in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            items.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "isDir": child.is_dir(),
                    "size": child.stat().st_size if child.is_file() else None,
                }
            )

        return {"path": str(p), "items": items}

    async def read_file(self, path: str, *, max_bytes: int = 200_000) -> Dict[str, Any]:
        p = self._resolve(path)
        if not p.exists():
            raise RuntimeError("文件不存在")
        if not p.is_file():
            raise RuntimeError("目标不是文件")

        data = p.read_bytes()
        truncated = len(data) > max_bytes
        data = data[:max_bytes]
        text = data.decode("utf-8", errors="replace")

        return {
            "path": str(p),
            "truncated": truncated,
            "content": text,
        }

    async def mkdir(self, path: str) -> Dict[str, Any]:
        p = self._resolve(path)
        p.mkdir(parents=True, exist_ok=True)
        return {"path": str(p)}

    async def delete(self, path: str) -> Dict[str, Any]:
        p = self._resolve(path)
        if not p.exists():
            return {"path": str(p), "deleted": False}
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return {"path": str(p), "deleted": True}

    async def move(self, src: str, dst: str) -> Dict[str, Any]:
        s = self._resolve(src)
        d = self._resolve(dst)
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
        return {"from": str(s), "to": str(d)}

    async def copy(self, src: str, dst: str) -> Dict[str, Any]:
        s = self._resolve(src)
        d = self._resolve(dst)
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            shutil.copytree(str(s), str(d), dirs_exist_ok=True)
        else:
            shutil.copy2(str(s), str(d))
        return {"from": str(s), "to": str(d)}
