"""通用基础工具：所有项目共用的最小能力，不依赖外部注入。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from ...sdk.decorator import build_tool, get_spec, tool
from ..levels import PermissionLevel

if TYPE_CHECKING:
    from ..loader import BuiltinFactory, ToolContext


@tool(
    name="echo",
    description="原样返回输入文本，用于连通性测试。",
    permission=PermissionLevel.FULL_TRUST,
)
def echo(text: str) -> str:
    return f"Echo: {text}"


@tool(
    name="get_time",
    description="返回当前 UTC 时间（ISO 格式）。",
    permission=PermissionLevel.AUTO_WITH_NOTIFICATION,
)
def get_time() -> str:
    return datetime.now(timezone.utc).isoformat()


@tool(
    name="simulate_delete_file",
    description="模拟删除文件（演示审批流，不真删）。",
    permission=PermissionLevel.ASK_FIRST,
    path_arg="path",
)
def simulate_delete_file(path: str) -> str:
    return f"[SIMULATED] Deleted: {path}"


def _factory(func: Callable[..., Any]) -> "BuiltinFactory":
    """无依赖的 @tool 函数 -> 统一的内置工厂签名（忽略上下文）。"""
    return lambda ctx: build_tool(func)


def _registered_name(func: Callable[..., Any]) -> str:
    spec = get_spec(func)
    assert spec is not None, f"{func} 缺少 @tool 元数据"
    return spec.name


_FUNCS = (echo, get_time, simulate_delete_file)

# 工具名 -> 工厂（装载器按名字取用）
TOOLS: dict[str, "BuiltinFactory"] = {_registered_name(f): _factory(f) for f in _FUNCS}
