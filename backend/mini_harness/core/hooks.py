# DOC: docs/10-custom-hooks.md
"""生命周期钩子：输入过滤、结果清洗、埋点等横切关注的唯一扩展点。

五个点位（引擎在关键路径上调用）：

    before_llm_call(messages, tools) -> (messages, tools)   请求发出前；**只影响本次请求，不回写账本**
    after_llm_call(response) -> response                    模型返回后、写账本前
    before_tool_execute(name, args) -> (allowed, reason)     工具校验通过、权限裁决**之前**；False 则拦截
    after_tool_execute(name, args, result) -> result         工具执行后、写回账本前（结果清洗）
    on_error(exc) -> None                                   引擎内的错误路径（观测用，不改变流程）

设计约束：
- 钩子异常**绝不崩溃引擎**：捕获后记录，由引擎转成 error 事件继续跑；
- 除 `before_tool_execute` 返回 False 外，钩子不改变控制流；
- 同一点位按配置顺序链式调用，前一个钩子的输出是后一个的输入；
- 钩子方法写成 `async` 或同步函数都可以（引擎两种都支持）。

`before_tool_execute` 放在权限裁决之前，是为了让"敏感词/危险操作"类钩子能在弹出审批
之前就拦掉，避免用无意义的审批打扰用户。
"""

from __future__ import annotations

import importlib
import inspect
import sys
from typing import Any, Optional

from .config import REPO_ROOT


class HarnessHooks:
    """钩子基类：全部方法默认 no-op，按需覆盖。"""

    async def before_llm_call(
        self, messages: list[dict], tools: Optional[list[dict]]
    ) -> tuple[list[dict], Optional[list[dict]]]:
        return messages, tools

    async def after_llm_call(self, response: Any) -> Any:
        return response

    async def before_tool_execute(self, name: str, args: dict) -> tuple[bool, str]:
        return True, ""

    async def after_tool_execute(self, name: str, args: dict, result: str) -> str:
        return result

    async def on_error(self, exc: BaseException) -> None:
        return None


class HookChain:
    """多个钩子的链式容器；同时收集钩子自身的执行失败，供引擎转成 error 事件。"""

    def __init__(self, hooks: Optional[list[HarnessHooks]] = None) -> None:
        self.hooks: list[HarnessHooks] = list(hooks or [])
        self._errors: list[str] = []

    def __len__(self) -> int:
        return len(self.hooks)

    # ----- 五个点位 -----
    async def before_llm_call(
        self, messages: list[dict], tools: Optional[list[dict]]
    ) -> tuple[list[dict], Optional[list[dict]]]:
        for hook in self.hooks:
            try:
                messages, tools = await _maybe_await(hook.before_llm_call(messages, tools))
            except Exception as e:  # noqa: BLE001 —— 钩子异常不得影响主流程
                self._fail(hook, "before_llm_call", e)
        return messages, tools

    async def after_llm_call(self, response: Any) -> Any:
        for hook in self.hooks:
            try:
                new_response = await _maybe_await(hook.after_llm_call(response))
            except Exception as e:  # noqa: BLE001
                self._fail(hook, "after_llm_call", e)
                continue
            if new_response is not None:
                response = new_response
        return response

    async def before_tool_execute(self, name: str, args: dict) -> tuple[bool, str]:
        for hook in self.hooks:
            try:
                allowed, reason = await _maybe_await(hook.before_tool_execute(name, args))
            except Exception as e:  # noqa: BLE001
                self._fail(hook, "before_tool_execute", e)
                continue
            if not allowed:
                return False, reason or f"被钩子 {type(hook).__name__} 拦截"
        return True, ""

    async def after_tool_execute(self, name: str, args: dict, result: str) -> str:
        for hook in self.hooks:
            try:
                new_result = await _maybe_await(hook.after_tool_execute(name, args, result))
            except Exception as e:  # noqa: BLE001
                self._fail(hook, "after_tool_execute", e)
                continue
            if new_result is not None:
                result = new_result
        return result

    async def on_error(self, exc: BaseException) -> None:
        for hook in self.hooks:
            try:
                await _maybe_await(hook.on_error(exc))
            except Exception as e:  # noqa: BLE001
                self._fail(hook, "on_error", e)

    # ----- 失败收集（引擎负责下发成 error 事件）-----
    def drain_errors(self) -> list[str]:
        errors, self._errors = self._errors, []
        return errors

    def _fail(self, hook: HarnessHooks, point: str, exc: BaseException) -> None:
        self._errors.append(f"{type(hook).__name__}.{point}: {exc!r}")


# ----------------------------------------------------------------------
# 从配置加载
# ----------------------------------------------------------------------
def load_hooks(paths: list[str]) -> HookChain:
    """按导入路径加载钩子，顺序即调用顺序。

    支持两种写法（等价）：`projects.default.hooks.LoggingHooks` / `projects.default.hooks:LoggingHooks`。
    路径不存在、类不存在、不是 HarnessHooks 子类，一律抛错 —— 配置错误要显式暴露。
    """
    hooks: list[HarnessHooks] = []
    for path in paths:
        obj = _import_object(path)
        hook = obj() if isinstance(obj, type) else obj
        if not isinstance(hook, HarnessHooks):
            raise TypeError(f"{path} 不是 HarnessHooks 的子类/实例")
        hooks.append(hook)
    return HookChain(hooks)


def _import_object(path: str) -> Any:
    if ":" in path:
        module_name, _, attr = path.partition(":")
    else:
        module_name, _, attr = path.rpartition(".")
    if not module_name or not attr:
        raise ValueError(f"非法钩子导入路径: {path}（应为 模块路径.类名 或 模块路径:类名）")
    module = _import_module(module_name)
    obj = getattr(module, attr, None)
    if obj is None:
        raise AttributeError(f"钩子不存在: {path}（模块 {module_name} 中找不到 {attr}）")
    return obj


def _import_module(module_name: str):
    try:
        return importlib.import_module(module_name)
    except ImportError:
        # 项目包（如 projects.<name>.hooks）不在 sys.path 上时，把仓库根加进去再试一次
        if str(REPO_ROOT) in sys.path:
            raise
        sys.path.insert(0, str(REPO_ROOT))
        return importlib.import_module(module_name)


async def _maybe_await(value: Any) -> Any:
    """允许钩子方法写成同步函数：返回值可直接使用。"""
    if inspect.isawaitable(value):
        return await value
    return value
