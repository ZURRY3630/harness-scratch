"""消息模型：统一内部表示，序列化为 OpenAI Chat 格式。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


@dataclass
class ToolCall:
    tool_name: str
    arguments: dict
    call_id: str = field(default_factory=new_id)

    def to_dict(self) -> dict:
        return {
            "id": self.call_id,
            "type": "function",
            "function": {
                "name": self.tool_name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: Optional[list[ToolCall]] = None
    tool_call_id: Optional[str] = None
    message_id: str = field(default_factory=new_id)
    # 元信息：token 估算值、摘要标记、创建时间
    token_estimate: int = 0
    is_summary: bool = False
    created_at: float = 0.0

    def to_openai(self) -> dict:
        """转为 OpenAI Chat 消息。

        保留旧版验证过的健壮性细节：
        - assistant 带 tool_calls 且 content 为空 -> 完全省略 content 键
        - tool 消息必须带 tool_call_id，否则丢弃（孤儿 tool 会导致 API 400）
        """
        if self.role == Role.ASSISTANT and self.tool_calls:
            msg: dict[str, Any] = {"role": "assistant", "tool_calls": [tc.to_dict() for tc in self.tool_calls]}
            if self.content:
                msg["content"] = self.content
            return msg
        if self.role == Role.TOOL:
            if not self.tool_call_id:
                return {"role": "user", "content": self.content}  # 不应发生；兜底转 user
            return {"role": "tool", "tool_call_id": self.tool_call_id, "content": self.content}
        return {"role": self.role.value, "content": self.content}
