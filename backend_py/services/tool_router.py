from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from backend_py.services.dev_runner import DevRunner
from backend_py.services.file_manager import FileManager
from backend_py.services.file_writer import FileWriter
from backend_py.services.douyin_controller import DouyinController
from backend_py.services.llm_service import LLMService
from backend_py.services.macos_media_control import MacOSMediaControl
from backend_py.services.music_controller import MusicController
from backend_py.services.system_controller import SystemController
from backend_py.services.wecom_service import WeComService
from backend_py.services.wecom_ui_controller import WeComUIController


class ToolRouter:
    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service
        self.system_controller = SystemController()
        self.music_controller = MusicController()
        self.file_writer = FileWriter()

        self.file_manager = FileManager()
        self.dev_runner = DevRunner()
        self.wecom_service = WeComService()
        self.media_control = MacOSMediaControl()

        # OCR-first UI 自动化控制器（高风险：会真实点击/键入）
        self.douyin_controller = DouyinController()
        self.wecom_ui_controller = WeComUIController()

    def get_supported_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": "play_music", "description": "播放音乐", "parameters": ["source", "query"]},
            {
                "name": "music_ui",
                "description": "通过 UI 自动化控制音乐播放器（需要确认）",
                "parameters": ["player", "action", "query", "pickMode", "debug", "dryRun"],
            },
            {
                "name": "douyin_ui",
                "description": "通过 UI 自动化控制抖音：搜索并播放最匹配视频（高风险，需要确认）",
                "parameters": ["query", "debug", "dryRun"],
            },
            {
                "name": "wecom_ui",
                "description": "通过 UI 自动化控制企业微信：搜索联系人并发送消息（高风险，需要确认）",
                "parameters": ["contactName", "message", "debug", "dryRun"],
            },
            {
                "name": "media_control",
                "description": "系统媒体控制兜底（媒体键/音量/当前曲目信息）",
                "parameters": ["action", "delta"],
            },
            {"name": "stop_music", "description": "停止音乐播放", "parameters": []},
            {"name": "open_app", "description": "打开应用程序", "parameters": ["name"]},
            {"name": "write_article", "description": "写文章", "parameters": ["topic", "style", "length"]},
            {"name": "write_file", "description": "写文件", "parameters": ["path", "content", "mode"]},
            {"name": "send_message", "description": "发送消息（企业微信）", "parameters": ["target", "content", "channel"]},
            {"name": "write_run_code", "description": "编写并运行代码", "parameters": ["language", "code", "run"]},
            {"name": "file_control", "description": "文件管理", "parameters": ["operation", "path", "destination"]},
            {"name": "run_tests", "description": "运行单测", "parameters": ["cwd", "command", "timeoutSec"]},
            {"name": "execute_workflow", "description": "执行多步工作流", "parameters": ["steps"]},
        ]

    @staticmethod
    def _parse_wecom_target(target: str) -> Tuple[str, str]:
        t = str(target or "").strip()
        if not t:
            return ("user", "")

        lowered = t.lower()
        for prefix, target_type in [
            ("user:", "user"),
            ("party:", "party"),
            ("tag:", "tag"),
            ("chat:", "chat"),
        ]:
            if lowered.startswith(prefix):
                return (target_type, t[len(prefix) :].strip())

        # 默认按 user 处理
        return ("user", t)

    @staticmethod
    def _tail(text: str, *, limit: int = 4000) -> str:
        if len(text) <= limit:
            return text
        return text[-limit:]

    async def route_and_execute(self, tool_call: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        name = tool_call.get("name")
        args = tool_call.get("arguments") or {}
        tool_call_id = tool_call.get("id")
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # 按 requestId/runId 路由调试产物目录（ui_debug/<runId>/）。
        session = context.get("session")
        run_id = None
        try:
            run_id = getattr(session, "current_request_id", None)
        except Exception:
            run_id = None
        run_id = str(run_id or tool_call_id or f"{name}_{int(time.time() * 1000)}").strip()
        if run_id:
            os.environ["VOICE_ASSISTANT_DEBUG_RUN"] = run_id

        try:
            if name == "execute_workflow":
                steps = args.get("steps")
                if not isinstance(steps, list) or not steps:
                    raise RuntimeError("steps 必须是非空数组")

                results = []
                for idx, step in enumerate(steps[:10]):
                    if not isinstance(step, dict):
                        raise RuntimeError("steps 元素必须是对象")
                    step_name = step.get("name")
                    step_args = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
                    if not isinstance(step_name, str) or not step_name:
                        raise RuntimeError("steps.name 无效")
                    if step_name == "execute_workflow":
                        raise RuntimeError("不允许嵌套 execute_workflow")

                    r = await self.route_and_execute(
                        {"id": f"{tool_call_id}_step_{idx}", "name": step_name, "arguments": step_args},
                        context,
                    )
                    results.append(r)
                    if not r.get("success"):
                        break

                ok = bool(results) and all(bool(r.get("success")) for r in results)
                msg = "工作流执行完成" if ok else "工作流执行失败"
                return {
                    "toolCallId": tool_call_id,
                    "name": name,
                    "success": ok,
                    "result": {"action": "execute_workflow", "message": msg, "steps": results} if ok else None,
                    "error": None if ok else (results[-1].get("error") if results else "工作流执行失败"),
                    "timestamp": ts,
                }

            if name == "music_ui":
                result = await self.music_controller.music_ui(
                    player=args.get("player"),
                    action=args.get("action"),
                    query=args.get("query"),
                    pick_mode=args.get("pickMode"),
                    debug=bool(args.get("debug") is True),
                    dry_run=bool(args.get("dryRun") is True),
                )
                return {
                    "toolCallId": tool_call_id,
                    "name": name,
                    "success": True,
                    "result": {"action": "music_ui", **result},
                    "timestamp": ts,
                }

            if name == "douyin_ui":
                result = await self.douyin_controller.search_and_play(
                    query=str(args.get("query") or "").strip(),
                    debug=bool(args.get("debug") is True),
                    dry_run=bool(args.get("dryRun") is True),
                )
                return {
                    "toolCallId": tool_call_id,
                    "name": name,
                    "success": True,
                    "result": {"action": "douyin_ui", **result},
                    "timestamp": ts,
                }

            if name == "wecom_ui":
                result = await self.wecom_ui_controller.search_contact_and_send(
                    contact_name=str(args.get("contactName") or "").strip(),
                    message=str(args.get("message") or ""),
                    debug=bool(args.get("debug") is True),
                    dry_run=bool(args.get("dryRun") is True),
                )
                return {
                    "toolCallId": tool_call_id,
                    "name": name,
                    "success": True,
                    "result": {"action": "wecom_ui", **result},
                    "timestamp": ts,
                }

            if name == "media_control":
                action = str(args.get("action") or "").strip().lower()
                if action == "get_now_playing":
                    details = await self.media_control.get_now_playing()
                    return {
                        "toolCallId": tool_call_id,
                        "name": name,
                        "success": True,
                        "result": {"action": "media_control", "details": details, "message": "已获取当前曲目信息"},
                        "timestamp": ts,
                    }

                if action == "set_volume_delta":
                    delta = int(args.get("delta") or 0)
                    details = await self.media_control.set_volume_delta(delta)
                    return {
                        "toolCallId": tool_call_id,
                        "name": name,
                        "success": True,
                        "result": {"action": "media_control", **details},
                        "timestamp": ts,
                    }

                details = await self.media_control.media_key(action)
                return {
                    "toolCallId": tool_call_id,
                    "name": name,
                    "success": True,
                    "result": {"action": "media_control", **details},
                    "timestamp": ts,
                }

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

            if name == "file_control":
                operation = args.get("operation")
                path_arg = str(args.get("path") or "")
                destination = str(args.get("destination") or "")

                if operation == "list":
                    details = await self.file_manager.list_dir(path_arg)
                    return {
                        "toolCallId": tool_call_id,
                        "name": name,
                        "success": True,
                        "result": {"action": "file_control", "operation": "list", "message": "目录已列出", "details": details},
                        "timestamp": ts,
                    }

                if operation == "read":
                    details = await self.file_manager.read_file(path_arg)
                    return {
                        "toolCallId": tool_call_id,
                        "name": name,
                        "success": True,
                        "result": {"action": "file_control", "operation": "read", "message": "文件已读取", "details": details},
                        "timestamp": ts,
                    }

                if operation == "mkdir":
                    details = await self.file_manager.mkdir(path_arg)
                    return {
                        "toolCallId": tool_call_id,
                        "name": name,
                        "success": True,
                        "result": {"action": "file_control", "operation": "mkdir", "message": "目录已创建", "details": details},
                        "timestamp": ts,
                    }

                if operation == "delete":
                    details = await self.file_manager.delete(path_arg)
                    return {
                        "toolCallId": tool_call_id,
                        "name": name,
                        "success": True,
                        "result": {"action": "file_control", "operation": "delete", "message": "已删除", "details": details},
                        "timestamp": ts,
                    }

                if operation == "move":
                    details = await self.file_manager.move(path_arg, destination)
                    return {
                        "toolCallId": tool_call_id,
                        "name": name,
                        "success": True,
                        "result": {"action": "file_control", "operation": "move", "message": "已移动", "details": details},
                        "timestamp": ts,
                    }

                if operation == "copy":
                    details = await self.file_manager.copy(path_arg, destination)
                    return {
                        "toolCallId": tool_call_id,
                        "name": name,
                        "success": True,
                        "result": {"action": "file_control", "operation": "copy", "message": "已复制", "details": details},
                        "timestamp": ts,
                    }

                raise RuntimeError("文件操作不支持")

            if name == "run_tests":
                cwd = str(args.get("cwd") or "").strip()
                cmd = str(args.get("command") or "").strip()
                timeout_sec = float(args.get("timeoutSec") or 120)

                # 与 FileWriter 行为对齐：允许使用 home 相对路径
                cwd_path = Path(cwd)
                resolved = str(cwd_path if cwd_path.is_absolute() else (Path.home() / cwd_path))

                details = await self.dev_runner.run(cwd=resolved, command=cmd, timeout_sec=timeout_sec)
                ok = int(details.get("exitCode") or 0) == 0
                msg = "单测通过" if ok else "单测失败"

                return {
                    "toolCallId": tool_call_id,
                    "name": name,
                    "success": ok,
                    "result": {"action": "run_tests", "message": msg, "details": details} if ok else None,
                    "error": None if ok else (details.get("stderrTail") or msg),
                    "timestamp": ts,
                }

            if name == "write_run_code":
                language = str(args.get("language") or "").strip().lower()
                code = str(args.get("code") or "")
                run = bool(args.get("run") is True)

                base_dir = Path.home() / "Documents" / "VoiceAssistant" / "code_runs"
                base_dir.mkdir(parents=True, exist_ok=True)

                ext_map = {"python": "py", "javascript": "js", "bash": "sh"}
                ext = ext_map.get(language)
                if not ext:
                    raise RuntimeError("不支持的语言")

                file_path = base_dir / f"run_{int(time.time() * 1000)}.{ext}"
                file_path.write_text(code, encoding="utf-8")

                if not run:
                    return {
                        "toolCallId": tool_call_id,
                        "name": name,
                        "success": True,
                        "result": {"action": "write_run_code", "message": "代码已保存", "filePath": str(file_path)},
                        "timestamp": ts,
                    }

                # 执行代码
                if language == "python":
                    cmd = f"python {file_path.name}"
                    cwd = str(base_dir)
                elif language == "javascript":
                    cmd = f"node {file_path.name}"
                    cwd = str(base_dir)
                else:
                    # bash 脚本
                    cmd = f"bash {file_path.name}"
                    cwd = str(base_dir)

                details = await self.dev_runner.run(cwd=cwd, command=cmd, timeout_sec=60.0)
                ok = int(details.get("exitCode") or 0) == 0
                msg = "代码运行完成" if ok else "代码运行失败"

                return {
                    "toolCallId": tool_call_id,
                    "name": name,
                    "success": ok,
                    "result": {"action": "write_run_code", "message": msg, "filePath": str(file_path), "details": details} if ok else None,
                    "error": None if ok else (details.get("stderrTail") or msg),
                    "timestamp": ts,
                }

            if name == "send_message":
                target = str(args.get("target") or "").strip()
                content = str(args.get("content") or "").strip()
                channel = str(args.get("channel") or "auto").strip().lower()
                ensure_app_open = True if args.get("ensureAppOpen") is None else bool(args.get("ensureAppOpen"))

                if channel not in {"auto", "im"}:
                    raise RuntimeError("当前仅支持企业微信发送（channel=im）")

                warn = None
                if ensure_app_open:
                    try:
                        await self.system_controller.open_application("企业微信")
                    except Exception as e:
                        warn = f"未能打开企业微信（将继续尝试通过 API 发送）：{e}"

                target_type, target_id = self._parse_wecom_target(target)
                if not target_id:
                    raise RuntimeError("消息目标为空")

                details = await self.wecom_service.send_text(
                    target_type=target_type,
                    target=target_id,
                    content=content,
                )

                msg = f"已通过企业微信发送消息（{target_type}:{target_id}）"
                if warn:
                    msg = f"{warn}；{msg}"

                return {
                    "toolCallId": tool_call_id,
                    "name": name,
                    "success": True,
                    "result": {"action": "send_message", "message": msg, "details": details},
                    "timestamp": ts,
                }

            raise RuntimeError(f"未知的工具: {name}")

        except Exception as e:
            return {
                "toolCallId": tool_call_id,
                "name": name,
                "success": False,
                "error": str(e),
                "timestamp": ts,
            }
