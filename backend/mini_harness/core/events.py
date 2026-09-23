"""类型化事件：引擎 -> 外部世界的唯一契约（取代文本前缀行协议）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    TURN_STARTED = "turn_started"
    DELTA = "delta"                        # 模型增量文本
    TOOL_CALL_STARTED = "tool_call_started"
    APPROVAL_REQUIRED = "approval_required"
    TOOL_EXECUTED = "tool_executed"
    TURN_FINISHED = "turn_finished"
    CONTEXT_COMPRESSED = "context_compressed"   # 压缩事件（/token 预算联动）
    MEMORY_SAVED = "memory_saved"               # 长期记忆写入
    BUDGET_EXCEEDED = "budget_exceeded"
    RUN_FINISHED = "run_finished"
    ERROR = "error"


@dataclass
class Event:
    type: EventType
    data: dict = field(default_factory=dict)
    session_id: str = ""

    def to_dict(self) -> dict:
        return {"type": self.type.value, "data": self.data, "session_id": self.session_id}


def ev(type_: EventType, session_id: str = "", **data: Any) -> Event:
    return Event(type=type_, data=data, session_id=session_id)
