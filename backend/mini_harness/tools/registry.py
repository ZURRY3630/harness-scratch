"""工具注册表：Schema 自动生成 + 参数强校验 + 路径边界（全部接线，不再有旁路）。"""

from __future__ import annotations

import inspect
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .levels import PermissionLevel

_TYPE_MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}


def generate_parameters_schema(func: Callable) -> dict:
    """函数签名 -> JSON Schema（解析失败退化为无参数 object）。"""
    try:
        sig = inspect.signature(func)
        hints = typing.get_type_hints(func)
    except (TypeError, ValueError):
        return {"type": "object", "properties": {}}

    properties: dict = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        py_type = hints.get(name, str)
        properties[name] = {"type": _TYPE_MAP.get(py_type, "string")}
        if param.default is inspect.Parameter.empty:
            required.append(name)
    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def validate_arguments(schema: dict, arguments: dict) -> tuple[bool, str]:
    """Schema 强校验：必填、未知字段、类型。失败信息直接回给模型（可行动）。"""
    props = schema.get("properties", {})
    for f in schema.get("required", []):
        if f not in arguments:
            return False, f"缺少必填参数: {f}（可用参数: {sorted(props)}）"
    for f, v in arguments.items():
        if f not in props:
            return False, f"未知参数: {f}（可用参数: {sorted(props)}）"
        expected = props[f].get("type", "string")
        type_ok = {
            "string": isinstance(v, str),
            "integer": isinstance(v, int) and not isinstance(v, bool),
            "number": isinstance(v, (int, float)) and not isinstance(v, bool),
            "boolean": isinstance(v, bool),
        }.get(expected, True)
        if not type_ok:
            return False, f"参数 {f} 类型错误: 期望 {expected}，得到 {type(v).__name__}"
    return True, ""


@dataclass
class Tool:
    name: str
    description: str
    func: Callable
    permission: PermissionLevel = PermissionLevel.ASK_FIRST
    parameters: Optional[dict] = None       # 手动 schema 优先
    path_arg: Optional[str] = None          # 哪个参数是路径（需边界校验）

    def schema(self) -> dict:
        params = self.parameters or generate_parameters_schema(self.func)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": params},
        }


class ToolRegistry:
    """工具唯一登记处。allow_tools / allowed_paths 是安全边界，注册与执行时都校验。"""

    def __init__(self, allow_tools: Optional[list[str]] = None, allowed_paths: Optional[list[str]] = None):
        self._tools: dict[str, Tool] = {}
        self.allow_tools = allow_tools
        self.allowed_paths = [Path(p).resolve() for p in (allowed_paths or [])]

    # ----- 注册 -----
    def register(self, tool: Tool) -> None:
        if self.allow_tools is not None and tool.name not in self.allow_tools:
            raise ValueError(f"工具 '{tool.name}' 不在白名单内，拒绝注册")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)

    # ----- 校验（执行链路强制调用）-----
    def validate_path(self, tool: Tool, arguments: dict) -> tuple[bool, str]:
        """路径参数必须在 allowed_paths 之内（resolve 后判断，防 ../ 与符号链接逃逸）。"""
        if not tool.path_arg or not self.allowed_paths:
            return True, ""
        raw = arguments.get(tool.path_arg)
        if not isinstance(raw, str) or not raw:
            return False, f"参数 {tool.path_arg} 必须是非空路径字符串"
        target = Path(raw).resolve()
        for base in self.allowed_paths:
            if target == base or target.is_relative_to(base):
                return True, ""
        return False, f"路径越界: {raw} 不在允许目录内"

    def validate_and_check(self, tool_name: str, arguments: dict) -> tuple[bool, str]:
        """幻觉工具检查 + Schema 强校验 + 路径边界，一次做完。"""
        tool = self._tools.get(tool_name)
        if not tool:
            return False, f"工具 '{tool_name}' 不存在（幻觉调用）"
        schema = tool.parameters or generate_parameters_schema(tool.func)
        ok, msg = validate_arguments(schema, arguments)
        if not ok:
            return False, msg
        return self.validate_path(tool, arguments)
