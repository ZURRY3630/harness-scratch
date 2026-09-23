"""示例钩子：把五个生命周期点位打到标准输出，用于验证接线与排查问题。

启用方式（config.yaml）：

    hooks:
      - projects.default.hooks.LoggingHooks

也可用冒号写法：`projects.default.hooks:LoggingHooks`。

注意：这是**示例**。真实项目请覆盖同一个 `HarnessHooks` 基类，写自己的过滤 / 清洗 / 埋点逻辑；
钩子里抛异常不会崩引擎，只会转成一条 error 事件。
"""

from __future__ import annotations

from typing import Any, Optional

from mini_harness.core.hooks import HarnessHooks


def _log(point: str, detail: str) -> None:
    print(f"[hooks] {point}: {detail}", flush=True)


class LoggingHooks(HarnessHooks):
    """只读钩子：不改数据、不拦截，仅记录每个点位被调用。"""

    async def before_llm_call(
        self, messages: list[dict], tools: Optional[list[dict]]
    ) -> tuple[list[dict], Optional[list[dict]]]:
        _log("before_llm_call", f"{len(messages)} 条消息 / {len(tools or [])} 个工具")
        return messages, tools

    async def after_llm_call(self, response: Any) -> Any:
        tool_names = [tc.tool_name for tc in (getattr(response, "tool_calls", None) or [])]
        _log("after_llm_call", f"content={len(response.content or '')} 字 / tool_calls={tool_names}")
        return response

    async def before_tool_execute(self, name: str, args: dict) -> tuple[bool, str]:
        _log("before_tool_execute", f"{name}({args})")
        return True, ""

    async def after_tool_execute(self, name: str, args: dict, result: str) -> str:
        _log("after_tool_execute", f"{name} -> {len(result)} 字")
        return result

    async def on_error(self, exc: BaseException) -> None:
        _log("on_error", f"{type(exc).__name__}: {exc}")
