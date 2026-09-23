from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Any
import uuid

class MessageRole(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"

@dataclass
class Message:
    role: MessageRole
    content: str
    tool_calls: Optional[List['ToolCall']] = None
    tool_call_id: Optional[str] = None
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

@dataclass
class ToolCall:
    tool_name: str
    arguments: dict
    call_id: str