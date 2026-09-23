"""OpenAI 兼容供应商：流式 + 重试 + Prompt 缓存友好。

Prompt 缓存说明：
- DeepSeek / OpenAI 等服务端自动做前缀缓存（Context Caching / prompt caching），
  无需显式 API —— 前缀逐字稳定即可命中。
- 本层职责：保证请求前缀稳定（不重排、不改写历史），并统计命中情况。
  DeepSeek 响应的 usage.prompt_cache_hit_tokens / prompt_cache_miss_tokens 可直接读取；
  其他兼容端点没有该字段时记 0。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import AsyncIterator, Optional

from openai import AsyncOpenAI

from ..core.message import ToolCall
from ..core.registry import register_provider
from .provider import BaseModelProvider, ModelResponse

_RETRY_STATUS = {429, 500, 502, 503, 504}


@register_provider("openai")
class OpenAIProvider(BaseModelProvider):
    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.0,
        max_retries: int = 2,
        request_timeout: float = 120.0,
        cache_stats=None,  # context.budget.Budget，可选，用于统计命中
    ):
        self.model = model or os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.temperature = temperature
        self.max_retries = max_retries
        self.request_timeout = request_timeout
        self._budget = cache_stats
        self.client = AsyncOpenAI(
            api_key=api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY"),
            base_url=base_url or os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL"),
            timeout=request_timeout,
        )

    # ---------- 核心 ----------
    async def chat(self, messages: list[dict], tools: Optional[list] = None) -> ModelResponse:
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        resp = await self._with_retry(lambda: self.client.chat.completions.create(**kwargs))
        choice = resp.choices[0]
        msg = choice.message
        usage = {}
        if getattr(resp, "usage", None):
            usage = {
                "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(resp.usage, "completion_tokens", 0) or 0,
                "cache_hit_tokens": getattr(resp.usage, "prompt_cache_hit_tokens", 0) or 0,
            }
            if self._budget is not None:
                self._budget.account_llm(usage["prompt_tokens"], usage["completion_tokens"])
                if usage.get("cache_hit_tokens"):
                    self._budget.account_cache_hit()
        return ModelResponse(
            content=msg.content,
            tool_calls=self._parse_tool_calls(msg),
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
        )

    async def chat_stream(self, messages: list[dict], tools: Optional[list] = None) -> AsyncIterator[dict]:
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        stream = await self._with_retry(lambda: self.client.chat.completions.create(**kwargs))
        content_parts: list[str] = []
        tool_calls_acc: dict[int, dict] = {}
        usage = {}
        finish_reason = "stop"

        async for chunk in stream:
            # usage 可能在最后一个带 choices 的 chunk 或独立 chunk 上，统一检查
            u = getattr(chunk, "usage", None)
            if u is not None and getattr(u, "prompt_tokens", None):
                usage = {
                    "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
                    "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
                    "cache_hit_tokens": getattr(u, "prompt_cache_hit_tokens", 0) or 0,
                }
                if self._budget is not None:
                    self._budget.account_llm(usage["prompt_tokens"], usage["completion_tokens"])
                    if usage.get("cache_hit_tokens"):
                        self._budget.account_cache_hit()
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta
            if delta and delta.content:
                content_parts.append(delta.content)
                yield {"delta": delta.content}
            if delta and delta.tool_calls:
                for tc in delta.tool_calls:
                    acc = tool_calls_acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        acc["id"] = tc.id
                    if tc.function and tc.function.name:
                        acc["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        acc["arguments"] += tc.function.arguments

        tool_calls = None
        if tool_calls_acc:
            tool_calls = []
            for i in sorted(tool_calls_acc):
                acc = tool_calls_acc[i]
                args = {}
                try:
                    args = json.loads(acc["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(
                    tool_name=acc["name"],
                    arguments=args,
                    call_id=acc["id"] or f"call_{uuid.uuid4().hex[:8]}",
                ))
        yield {"response": ModelResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )}

    # ---------- 基础设施 ----------
    async def _with_retry(self, fn):
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await fn()
            except Exception as e:  # noqa: BLE001 —— 统一退避重试
                status = getattr(e, "status_code", None)
                retriable = status in _RETRY_STATUS or isinstance(e, (asyncio.TimeoutError, ConnectionError))
                last_exc = e
                if not retriable or attempt == self.max_retries:
                    raise
                await asyncio.sleep(0.5 * (2 ** attempt))
        raise last_exc  # pragma: no cover

    @staticmethod
    def _parse_tool_calls(msg) -> Optional[list[ToolCall]]:
        if not getattr(msg, "tool_calls", None):
            return None
        out = []
        for tc in msg.tool_calls:
            call_id = tc.id or f"call_{uuid.uuid4().hex[:8]}"
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            out.append(ToolCall(tool_name=tc.function.name, arguments=args, call_id=call_id))
        return out
