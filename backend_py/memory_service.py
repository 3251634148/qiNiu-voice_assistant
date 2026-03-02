"""本地 LLM 长期记忆服务。

功能：
- user_profile（长期偏好，JSON）：存储用户姓名、喜好、常用表达等
- rolling_summary（滚动摘要，短文本）：每轮对话后更新，保留主线信息
- 持久化到本地文件（~/.voice_assistant/memory/）

每次调用 LLM 时注入记忆上下文到 system prompt：
  system: 助手总规则 + USER_PROFILE + ROLLING_SUMMARY
  history: recent_messages（最近 N 轮）
  user: 当前输入
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend_py.config import settings

logger = logging.getLogger("backend_py.memory")

# 滚动摘要更新提示词（每轮对话后调用一次 LLM 生成新摘要）
SUMMARY_UPDATE_PROMPT = (
    "你将根据\u201c上一份摘要\u201d和\u201c最新一轮对话\u201d更新滚动摘要。目标：保留后续对话真正需要的主线信息，删除闲聊细节。\n"
    "要求：\n"
    "- 只保留已确认的事实、用户目标、偏好变化、任务状态（已做/待做/待确认）、重要约束。\n"
    "- 不要加入臆测。\n"
    "- 输出 200-500 字以内中文。\n"
    "- 直接输出摘要文本，不要添加任何前缀或标签。\n\n"
)

# 偏好抽取提示词（当用户明确说\u201c以后…\u201d时触发）
PREFERENCE_EXTRACT_PROMPT = (
    "从用户消息中抽取\u201c明确且稳定的偏好\u201d，合并到现有 USER_PROFILE。\n"
    "不要保存敏感隐私或一次性信息。无法确定则不填。\n"
    "仅输出合并后的 JSON（不要添加 Markdown 代码块标记）。\n\n"
)

# 触发偏好抽取的关键词
PREFERENCE_TRIGGERS = [
    "以后", "从今以后", "记住", "我叫", "我的名字", "叫我",
    "我喜欢", "我不喜欢", "我偏好", "我习惯", "别再", "不要再",
    "每次都", "总是帮我", "默认", "我常用",
]


class MemoryService:
    """管理用户长期记忆（偏好 + 滚动摘要）。"""

    def __init__(self) -> None:
        self.memory_dir = Path(settings.memory_dir)
        self.enabled = settings.memory_enabled
        self._profile_path = self.memory_dir / "user_profile.json"
        self._summary_path = self.memory_dir / "rolling_summary.txt"

        if self.enabled:
            self.memory_dir.mkdir(parents=True, exist_ok=True)
            logger.info("记忆服务已启用，存储目录: %s", self.memory_dir)

    def load_user_profile(self) -> Dict[str, Any]:
        """加载用户偏好档案。"""
        if not self.enabled or not self._profile_path.exists():
            return {}
        try:
            return json.loads(self._profile_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("加载 user_profile 失败: %s", e)
            return {}

    def save_user_profile(self, profile: Dict[str, Any]) -> None:
        """保存用户偏好档案。"""
        if not self.enabled:
            return
        try:
            self._profile_path.write_text(
                json.dumps(profile, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("保存 user_profile 失败: %s", e)

    def load_rolling_summary(self) -> str:
        """加载滚动摘要。"""
        if not self.enabled or not self._summary_path.exists():
            return ""
        try:
            return self._summary_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning("加载 rolling_summary 失败: %s", e)
            return ""

    def save_rolling_summary(self, summary: str) -> None:
        """保存滚动摘要。"""
        if not self.enabled:
            return
        try:
            self._summary_path.write_text(summary.strip(), encoding="utf-8")
        except Exception as e:
            logger.warning("保存 rolling_summary 失败: %s", e)

    def build_memory_context(self) -> str:
        """构建记忆上下文文本，用于注入 system prompt。

        返回格式:
          [用户档案]
          ...
          [对话摘要]
          ...
        """
        if not self.enabled:
            return ""

        parts: list[str] = []

        profile = self.load_user_profile()
        if profile:
            parts.append("[用户档案]")
            parts.append(json.dumps(profile, ensure_ascii=False, indent=2))

        summary = self.load_rolling_summary()
        if summary:
            parts.append("[对话摘要]")
            parts.append(summary)

        if not parts:
            return ""

        return "\n".join(parts)

    def should_extract_preferences(self, user_message: str) -> bool:
        """判断是否应该触发偏好抽取。"""
        if not self.enabled:
            return False
        text = str(user_message or "").strip()
        return any(trigger in text for trigger in PREFERENCE_TRIGGERS)

    def build_summary_update_messages(
        self,
        latest_messages: List[Dict[str, str]],
    ) -> List[Dict[str, str]]:
        """构建摘要更新的 LLM messages。

        调用方应将返回的 messages 发送给 LLM，获取更新后的摘要文本。
        """
        profile = self.load_user_profile()
        previous_summary = self.load_rolling_summary()

        # 格式化最近对话
        msg_lines: list[str] = []
        for m in latest_messages[-10:]:
            role = m.get("role", "user")
            content = str(m.get("content", ""))[:500]
            msg_lines.append(f"- {role}: {content}")

        user_content = (
            f"{SUMMARY_UPDATE_PROMPT}"
            f"USER_PROFILE:\n{json.dumps(profile, ensure_ascii=False) if profile else '{}'}\n\n"
            f"PREVIOUS_SUMMARY:\n{previous_summary or '（无）'}\n\n"
            f"LATEST_MESSAGES（按时间顺序）:\n" + "\n".join(msg_lines) + "\n\n"
            "输出 UPDATED_SUMMARY："
        )

        return [{"role": "user", "content": user_content}]

    def build_preference_extract_messages(
        self,
        user_message: str,
    ) -> List[Dict[str, str]]:
        """构建偏好抽取的 LLM messages。

        调用方应将返回的 messages 发送给 LLM，获取更新后的 user_profile JSON。
        """
        profile = self.load_user_profile()

        user_content = (
            f"{PREFERENCE_EXTRACT_PROMPT}"
            f"USER_PROFILE:\n{json.dumps(profile, ensure_ascii=False) if profile else '{}'}\n\n"
            f"USER_MESSAGE:\n{user_message}\n\n"
            "输出 JSON："
        )

        return [{"role": "user", "content": user_content}]

    def parse_and_save_profile(self, llm_response: str) -> Optional[Dict[str, Any]]:
        """解析 LLM 返回的 JSON 并保存为 user_profile。"""
        text = str(llm_response or "").strip()
        # 去掉可能的 Markdown 代码块标记
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        try:
            profile = json.loads(text)
            if isinstance(profile, dict):
                self.save_user_profile(profile)
                logger.info("用户偏好已更新: %s", list(profile.keys()))
                return profile
        except json.JSONDecodeError as e:
            logger.warning("偏好抽取 JSON 解析失败: %s", e)
        return None
