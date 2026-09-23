"""场景 3 验证用钩子：覆盖 before_llm_call，打印消息数与工具数。"""

from __future__ import annotations

from typing import Any, Optional

from mini_harness.core.hooks import HarnessHooks


class LoggingHooks(HarnessHooks):
    async def before_llm_call(
        self, messages: list[dict], tools: Optional[list[dict]]
    ) -> tuple[list[dict], Optional[list[dict]]]:
        print(f"[hooks] before_llm_call: 消息 {len(messages)} 条 / 工具 {len(tools or [])} 个", flush=True)
        return messages, tools

    async def after_tool_execute(self, name: str, args: dict, result: str) -> str:
        print(f"[hooks] after_tool_execute: {name} -> {result[:40]}", flush=True)
        return result

    async def on_error(self, exc: BaseException) -> None:
        print(f"[hooks] on_error: {type(exc).__name__}: {exc}", flush=True)
