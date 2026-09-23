from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional
from core.message import ToolCall

@dataclass
class ModelResponse:
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    finish_reason: str = "stop"

class BaseModelProvider(ABC):
    @abstractmethod
    async def chat(self, messages: List[dict], tools: Optional[list] = None) -> ModelResponse:
        """发送消息给模型，返回结构化响应。"""
        ...