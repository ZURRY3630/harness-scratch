"""`@tool` 装饰器：插件作者与内核之间的**唯一契约**。

用法（项目工具 / 插件工具）：

    from mini_harness.sdk.decorator import tool
    from mini_harness.tools.levels import PermissionLevel

    @tool(name="read_file", description="读取文件内容。", permission=PermissionLevel.ASK_FIRST, path_arg="path")
    def read_file(path: str) -> str:
        ...

约定：
- 参数 JSON Schema 由函数签名自动生成（仅支持 str/int/float/bool）；
  需要枚举、描述等更复杂的 schema 时显式传 `parameters=`；
- 函数为同步函数（内核在线程池里执行并做超时保护），返回值会被 `str()` 化；
- 装饰器只挂载元数据，不改变函数本身的行为与调用方式。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..tools.levels import PermissionLevel
from ..tools.registry import Tool

# 元数据挂载属性名
_METADATA_ATTR = "__harness_tool__"


@dataclass
class ToolSpec:
    """`@tool` 声明的静态元数据（不含函数体）。"""

    name: str
    description: str
    permission: PermissionLevel = PermissionLevel.ASK_FIRST
    parameters: Optional[dict] = None   # 手动 schema，优先于签名推导
    path_arg: Optional[str] = None      # 哪个参数是路径（执行链路做边界校验）


def tool(
    name: str,
    description: str,
    *,
    permission: PermissionLevel = PermissionLevel.ASK_FIRST,
    parameters: Optional[dict] = None,
    path_arg: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """把普通函数标记为工具。"""

    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(
            func,
            _METADATA_ATTR,
            ToolSpec(
                name=name,
                description=description,
                permission=permission,
                parameters=parameters,
                path_arg=path_arg,
            ),
        )
        return func

    return decorate


def get_spec(func: Callable[..., Any]) -> Optional[ToolSpec]:
    """取装饰器挂载的元数据；非工具函数返回 None。"""
    spec = getattr(func, _METADATA_ATTR, None)
    return spec if isinstance(spec, ToolSpec) else None


def is_tool(func: Callable[..., Any]) -> bool:
    """是否为 `@tool` 装饰过的函数。"""
    return get_spec(func) is not None


def build_tool(func: Callable[..., Any]) -> Tool:
    """`@tool` 函数 -> `Tool`（schema 生成复用 ToolRegistry 的逻辑，不重复实现）。"""
    spec = get_spec(func)
    if spec is None:
        raise ValueError(f"函数 {getattr(func, '__name__', func)!r} 未使用 @tool 装饰，无法注册为工具")
    return Tool(
        name=spec.name,
        description=spec.description,
        func=func,
        permission=spec.permission,
        parameters=spec.parameters,
        path_arg=spec.path_arg,
    )
