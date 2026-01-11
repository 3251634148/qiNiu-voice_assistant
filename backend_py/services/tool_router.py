from __future__ import annotations

import time
from typing import Any, Dict, Optional

from backend_py.services.file_writer import FileWriter
from backend_py.services.llm_service import LLMService
from backend_py.services.music_controller import MusicController
from backend_py.services.system_controller import SystemController


class ToolRouter:
    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service
        self.system_controller = SystemController()
        self.music_controller = MusicController()
        self.file_writer = FileWriter()

    def get_supported_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": "play_music", "description": "播放音乐", "parameters": ["source", "query"]},
            {"name": "stop_music", "description": "停止音乐播放", "parameters": []},
            {"name": "open_app", "description": "打开应用程序", "parameters": ["name"]},
            {"name": "write_article", "description": "写文章", "parameters": ["topic", "style", "length"]},
            {"name": "write_file", "description": "写文件", "parameters": ["path", "content", "mode"]},
            {"name": "send_message", "description": "发送消息", "parameters": ["target", "content", "channel"]},
            {"name": "write_run_code", "description": "编写并运行代码", "parameters": ["language", "code", "run"]},
            {"name": "file_control", "description": "文件管理", "parameters": ["operation", "path", "destination"]},
        ]

    async def route_and_execute(self, tool_call: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        name = tool_call.get("name")
        args = tool_call.get("arguments") or {}
        tool_call_id = tool_call.get("id")
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        try:
            if name == "play_music":
                result = await self.music_controller.play_music(
                    source=args.get("source"),
                    query=args.get("query"),
                )
                return {
                    "toolCallId": tool_call_id,
                    "name": name,
                    "success": True,
                    "result": {"action": "play_music", **result},
                    "timestamp": ts,
                }

            if name == "stop_music":
                result = await self.music_controller.stop_music()
                return {
                    "toolCallId": tool_call_id,
                    "name": name,
                    "success": True,
                    "result": {"action": "stop_music", **result},
                    "timestamp": ts,
                }

            if name == "open_app":
                app_name = args.get("name")
                details = await self.system_controller.open_application(str(app_name))
                return {
                    "toolCallId": tool_call_id,
                    "name": name,
                    "success": True,
                    "result": {
                        "action": "open_app",
                        "name": app_name,
                        "message": f"已打开应用程序: {app_name}",
                        "details": details,
                    },
                    "timestamp": ts,
                }

            if name == "write_file":
                file_path = args.get("path")
                content = args.get("content")
                mode = args.get("mode") or "overwrite"
                write_res = await self.file_writer.write_file(str(file_path), str(content), str(mode))
                return {
                    "toolCallId": tool_call_id,
                    "name": name,
                    "success": True,
                    "result": {
                        "action": "write_file",
                        "path": str(file_path),
                        "message": f"文件已写入: {write_res['filePath']}",
                        "details": write_res,
                    },
                    "timestamp": ts,
                }

            if name == "write_article":
                topic = args.get("topic")
                style = args.get("style") or "casual"
                length = args.get("length") or "short"
                prompt = (
                    f"请写一篇关于\"{topic}\"的文章。风格: {style}。长度: {length}。\n"
                    "要求：内容连贯、有结构、适合直接朗读。"
                )
                resp = await self.llm_service.invoke_llm([{"role": "user", "content": prompt}], max_tokens=1024)
                article_text = self.llm_service.extract_say_text(resp.get("text", ""))
                draft = await self.file_writer.save_draft(article_text, f"article_{int(time.time() * 1000)}.txt")
                return {
                    "toolCallId": tool_call_id,
                    "name": name,
                    "success": True,
                    "result": {
                        "action": "write_article",
                        "topic": topic,
                        "style": style,
                        "length": length,
                        "content": article_text,
                        "draftPath": draft["filePath"],
                        "message": f"已完成关于\"{topic}\"的文章写作，已保存为草稿",
                    },
                    "timestamp": ts,
                }

            if name == "send_message":
                raise RuntimeError("发送消息能力尚未接入")

            if name == "write_run_code":
                raise RuntimeError("编写并运行代码能力尚未接入")

            if name == "file_control":
                raise RuntimeError("文件管理能力尚未接入")

            raise RuntimeError(f"未知的工具: {name}")

        except Exception as e:
            return {
                "toolCallId": tool_call_id,
                "name": name,
                "success": False,
                "error": str(e),
                "timestamp": ts,
            }
