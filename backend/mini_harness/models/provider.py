# DOC: docs/08-custom-provider.md
"""模型供应商抽象：chat（工具调用）+ chat_text（内部用，如压缩摘要）+ 流式。"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Optional

from ..core.message import ToolCall


@dataclass
class ModelResponse:
    content: Optional[str] = None
    tool_calls: Optional[list[ToolCall]] = None
    finish_reason: str = "stop"
    usage: dict = field(default_factory=dict)  # {"prompt_tokens":int,"completion_tokens":int}


class BaseModelProvider(abc.ABC):
    """供应商接口。实现必须：
    - 解析 tool_calls 并兜底 call_id
    - 容忍参数 JSON 解析失败（退 {}）
    - 上报 usage（可得时）
    """

    @abc.abstractmethod
    async def chat(self, messages: list[dict], tools: Optional[list] = None) -> ModelResponse:
        ...

    @abc.abstractmethod
    async def chat_stream(self, messages: list[dict], tools: Optional[list] = None):
        """流式接口：async 逐块产出 {"delta": str}，结束时产出 {"response": ModelResponse}。"""
        ...

    async def chat_text(self, messages: list[dict]) -> str:
        """无工具的纯文本调用（压缩摘要等内部用途）。"""
        resp = await self.chat(messages, tools=None)
        return resp.content or ""
