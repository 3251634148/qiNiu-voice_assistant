from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class RiskAssessment:
    allowed: bool
    risk_level: str
    reason: str
    requires_confirmation: bool


class SafetyService:
    """安全校验（与 `backend/utils/safety.js` 行为对齐的最小移植版）。"""

    def __init__(self) -> None:
        home = str(Path.home())
        self.allowed_apps = {
            "spotify",
            "music",
            "itunes",
            "chrome",
            "safari",
            "firefox",
            "visual studio code",
            "vscode",
            "terminal",
            "finder",
            "calculator",
            "calendar",
            "mail",
            "notes",
            "pages",
            "numbers",
            "keynote",
            "preview",
            "photos",
            "system preferences",
            "activity monitor",
            "textedit",
            "reminder",
            "maps",
            "weather",
            "contacts",
        }

        self.allowed_directories = [
            os.path.join(home, "Documents"),
            os.path.join(home, "Desktop"),
            os.path.join(home, "Downloads"),
            os.path.join(home, "Music"),
            os.path.join(home, "Pictures"),
            os.path.join(home, "Movies"),
            os.path.join(home, "Documents", "VoiceAssistant"),
            "/tmp",
            "/var/tmp",
        ]

        self.dangerous_extensions = {
            ".exe",
            ".bat",
            ".cmd",
            ".com",
            ".pif",
            ".scr",
            ".vbs",
            ".js",
            ".jar",
            ".app",
            ".dmg",
            ".pkg",
            ".mpkg",
            ".deb",
            ".rpm",
            ".run",
            ".bin",
        }

    def _is_path_allowed(self, file_path: str) -> RiskAssessment:
        if not file_path or not isinstance(file_path, str):
            return RiskAssessment(False, "high", "文件路径无效", True)

        if ".." in file_path or "~" in file_path:
            return RiskAssessment(False, "high", "文件路径包含不安全字符", True)

        p = Path(file_path)
        abs_path = p if p.is_absolute() else (Path.home() / p)
        abs_path_str = str(abs_path)

        is_allowed = any(abs_path_str.startswith(d) for d in self.allowed_directories)
        if not is_allowed:
            return RiskAssessment(False, "high", "文件路径不在允许的目录范围内", True)

        ext = p.suffix.lower()
        if ext and ext in self.dangerous_extensions:
            return RiskAssessment(False, "high", f"不允许的文件类型: {ext}", True)

        return RiskAssessment(True, "medium", "ok", True)

    def validate_tool_call(self, tool_call: Dict[str, Any], *, allow_local_control: bool = True) -> RiskAssessment:
        name = tool_call.get("name")
        args = tool_call.get("arguments") or {}

        if not isinstance(name, str) or not name:
            return RiskAssessment(False, "high", "工具名无效", False)

        local_control_tools = {
            "write_file",
            "open_app",
            "file_control",
            "write_run_code",
            "send_message",
            "run_tests",
            "execute_workflow",
            "music_ui",
            "media_control",
        }
        if allow_local_control is False and name in local_control_tools:
            return RiskAssessment(False, "high", "本地应用操控已被关闭", False)

        # 按工具类型做参数与风险校验
        if name == "open_app":
            app_name = args.get("name")
            if not app_name or not isinstance(app_name, str) or not app_name.strip():
                return RiskAssessment(False, "medium", "缺少应用程序名称参数", False)
            trimmed = app_name.strip()
            if "/" in trimmed or "\\" in trimmed:
                return RiskAssessment(False, "high", "应用程序名称不应包含路径分隔符", False)
            if re.search(r"\r|\n|\0", trimmed) or re.search(r"[;&|<>]", trimmed):
                return RiskAssessment(False, "high", "应用程序名称包含不安全字符", False)
            return RiskAssessment(True, "low", "ok", False)

        if name == "write_file":
            file_path = args.get("path")
            content = args.get("content")
            if not file_path or not isinstance(file_path, str):
                return RiskAssessment(False, "high", "缺少文件路径参数", True)
            if content is None or not isinstance(content, str):
                return RiskAssessment(False, "high", "缺少文件内容参数", True)
            path_check = self._is_path_allowed(file_path)
            if not path_check.allowed:
                return path_check
            return RiskAssessment(True, "medium", "ok", True)

        if name == "file_control":
            operation = args.get("operation")
            path_arg = args.get("path")
            if operation not in {"list", "read", "move", "copy", "delete", "mkdir"}:
                return RiskAssessment(False, "high", "文件操作类型不支持或缺失", True)
            path_check = self._is_path_allowed(path_arg)
            if not path_check.allowed:
                return path_check
            dst = args.get("destination")
            if dst:
                dst_check = self._is_path_allowed(dst)
                if not dst_check.allowed:
                    return RiskAssessment(False, "high", f"目标路径不安全: {dst_check.reason}", True)
            return RiskAssessment(True, "high", "ok", True)

        if name == "write_run_code":
            language = args.get("language")
            code = args.get("code")
            run = args.get("run")
            if language not in {"javascript", "python", "bash"}:
                return RiskAssessment(False, "high", "代码语言不支持或缺失", True)
            if not code or not isinstance(code, str):
                return RiskAssessment(False, "high", "代码内容无效或缺失", True)
            if run is not None and not isinstance(run, bool):
                return RiskAssessment(False, "high", "run 参数必须是布尔值", True)
            return RiskAssessment(True, "high", "ok", True)

        if name == "send_message":
            target = args.get("target")
            content = args.get("content")
            if not target or not isinstance(target, str):
                return RiskAssessment(False, "medium", "缺少消息目标参数", True)
            if not content or not isinstance(content, str):
                return RiskAssessment(False, "medium", "缺少消息内容参数", True)
            return RiskAssessment(True, "medium", "ok", True)

        if name == "music_ui":
            player = args.get("player")
            action = args.get("action")
            if player not in {"kugou", "apple_music"}:
                return RiskAssessment(False, "high", "player 仅支持 kugou / apple_music", True)
            if action not in {"favorites_first", "playlist", "search"}:
                return RiskAssessment(False, "high", "action 仅支持 favorites_first / playlist / search", True)

            # playlist/search 需要 query
            query = args.get("query")
            if action in {"playlist", "search"} and (not isinstance(query, str) or not query.strip()):
                return RiskAssessment(False, "high", "playlist/search 需要提供 query", True)

            # UI 自动化属于高风险（会真实点击屏幕）
            return RiskAssessment(True, "high", "ok", True)

        if name == "media_control":
            action = args.get("action")
            if action not in {
                "play_pause",
                "next",
                "previous",
                "volume_up",
                "volume_down",
                "mute",
                "get_now_playing",
                "set_volume_delta",
            }:
                return RiskAssessment(False, "medium", "不支持的 media_control.action", False)

            if action == "set_volume_delta":
                delta = args.get("delta")
                if delta is None:
                    return RiskAssessment(False, "medium", "set_volume_delta 需要 delta 参数", False)
                try:
                    int(delta)
                except Exception:
                    return RiskAssessment(False, "medium", "delta 必须是整数", False)

            return RiskAssessment(True, "low", "ok", False)

        if name == "run_tests":
            cwd = args.get("cwd")
            command = args.get("command")
            if not cwd or not isinstance(cwd, str):
                return RiskAssessment(False, "high", "缺少工作目录参数", True)
            if not command or not isinstance(command, str):
                return RiskAssessment(False, "high", "缺少测试命令参数", True)
            cwd_check = self._is_path_allowed(cwd)
            if not cwd_check.allowed:
                return RiskAssessment(False, "high", f"工作目录不安全: {cwd_check.reason}", True)
            return RiskAssessment(True, "high", "ok", True)

        if name == "execute_workflow":
            steps = args.get("steps")
            if not isinstance(steps, list) or not steps:
                return RiskAssessment(False, "high", "steps 必须是非空数组", True)

            # 汇总每一步的风险：任一步不允许则整体不允许；任一步需要确认则整体需要确认。
            max_level = "low"
            need_confirm = False
            for step in steps[:10]:
                if not isinstance(step, dict):
                    return RiskAssessment(False, "high", "steps 元素必须是对象", True)
                step_name = step.get("name")
                step_args = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
                if not isinstance(step_name, str) or not step_name:
                    return RiskAssessment(False, "high", "steps.name 无效", True)
                if step_name == "execute_workflow":
                    return RiskAssessment(False, "high", "不允许嵌套 execute_workflow", True)

                r = self.validate_tool_call({"name": step_name, "arguments": step_args}, allow_local_control=allow_local_control)
                if not r.allowed:
                    return RiskAssessment(False, r.risk_level, f"工作流步骤被阻止: {r.reason}", r.requires_confirmation)

                # risk level merge
                order = {"low": 0, "medium": 1, "high": 2}
                if order.get(r.risk_level, 2) > order.get(max_level, 0):
                    max_level = r.risk_level
                need_confirm = need_confirm or r.requires_confirmation

            return RiskAssessment(True, max_level, "ok", need_confirm)

        # 默认风险表
        risk_table = {
            "play_music": ("low", False),
            "stop_music": ("low", False),
            "open_app": ("low", False),
            "write_article": ("medium", False),
        }
        if name in risk_table:
            level, confirm = risk_table[name]
            return RiskAssessment(True, level, "ok", confirm)

        return RiskAssessment(False, "high", f"未知的工具类型: {name}", False)

    def generate_confirmation_request(self, tool_call: Dict[str, Any], risk: RiskAssessment) -> Dict[str, Any]:
        # 保持与 Node 侧 SafetyService.generateConfirmationRequest 的 schema 兼容。
        import time
        import random

        confirmation_id = f"confirm_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
        name = tool_call.get("name")
        args = tool_call.get("arguments") or {}

        suggestions: List[str] = []
        if name == "write_file":
            suggestions.append(f"将写入文件: {args.get('path')}")
        elif name == "send_message":
            suggestions.append(f"将发送消息给: {args.get('target')}")
        elif name == "write_run_code":
            suggestions.append("此操作可能执行本地代码，请确认是否继续")
        elif name == "file_control":
            suggestions.append("此操作可能影响本地文件，请确认是否继续")
        elif name == "music_ui":
            suggestions.append("此操作将通过 UI 自动化点击屏幕并控制播放器，请确认是否继续")

        return {
            "id": confirmation_id,
            "toolCall": {
                "id": tool_call.get("id"),
                "name": name,
                "arguments": args,
            },
            "riskLevel": risk.risk_level,
            "summary": self.summarize_tool_call(name, args),
            "reason": risk.reason,
            "suggestions": suggestions,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "expiresAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 300)),
        }

    def summarize_tool_call(self, name: str, args: Dict[str, Any]) -> str:
        if name == "open_app":
            return f"打开应用程序: {args.get('name')}"
        if name == "write_file":
            return f"写文件: {args.get('path')}"
        if name == "send_message":
            return f"发送消息给: {args.get('target')}"
        if name == "music_ui":
            return f"UI 自动化音乐控制: {args.get('player')} / {args.get('action')} / {args.get('query')}"
        if name == "media_control":
            return f"系统媒体控制: {args.get('action')}"
        if name == "write_run_code":
            run = "并运行" if args.get("run") else ""
            return f"编写{args.get('language')}代码{run}"
        if name == "file_control":
            dst = args.get("destination")
            dst_text = f" → {dst}" if dst else ""
            return f"文件操作: {args.get('operation')} {args.get('path')}{dst_text}"
        if name == "run_tests":
            return f"运行单测: {args.get('command')}（目录: {args.get('cwd')}）"
        if name == "execute_workflow":
            steps = args.get("steps")
            step_names = []
            if isinstance(steps, list):
                for s in steps[:5]:
                    if isinstance(s, dict) and isinstance(s.get("name"), str):
                        step_names.append(s.get("name"))
            joined = " -> ".join(step_names) if step_names else "(unknown)"
            return f"执行多步工作流: {joined}"
        return f"执行操作: {name}"
