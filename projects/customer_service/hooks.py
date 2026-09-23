# DOC: docs/10-custom-hooks.md
"""项目钩子：工具结果脱敏 + 调用埋点。

脱敏为什么放在 `after_tool_execute`：工具返回的原文会进入对话账本并回灌给模型，
在写账本之前清洗，才能真正保证敏感信息不落库、不出现在后续上下文里。
"""

from __future__ import annotations

import re

from mini_harness.core.hooks import HarnessHooks

_PHONE = re.compile(r"1[3-9]\d{9}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _log(msg: str) -> None:
    print(f"[customer_service] {msg}", flush=True)


class PIIMaskHooks(HarnessHooks):
    """把工具结果里的手机号、邮箱打码，并记录每次 LLM 调用的规模。"""

    async def after_tool_execute(self, name: str, args: dict, result: str) -> str:
        masked = _PHONE.sub(lambda m: m.group()[:3] + "****" + m.group()[-4:], result)
        masked = _EMAIL.sub("***@***", masked)
        if masked != result:
            _log(f"工具 {name} 的返回内容已脱敏")
        return masked

    async def before_llm_call(self, messages, tools):
        _log(f"调用模型：{len(messages)} 条消息 / {len(tools or [])} 个工具")
        return messages, tools

    async def on_error(self, exc: BaseException) -> None:
        _log(f"引擎错误：{type(exc).__name__}: {exc}")
