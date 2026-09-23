import inspect
from dataclasses import dataclass
from typing import Callable, Dict, Optional, get_type_hints, List
from enum import Enum
from pathlib import Path
from tools.permission import PermissionLevel, PermissionGate  # noqa: F401  re-export



_TYPE_MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}




def _generate_parameters_schema(func: Callable) -> dict:
    """根据函数签名生成 JSON Schema；解析失败时退化为无参数 object。"""
    try:
        sig = inspect.signature(func)
        hints = get_type_hints(func)
    except (TypeError, ValueError):
        return {"type": "object", "properties": {}}

    properties = {}
    required = []
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


@dataclass
class Tool:
    name: str
    description: str
    func: Callable
    # permission: PermissionLevel | None = None  # 默认需要确认执行
    permission: PermissionLevel = PermissionLevel.ASK_FIRST
    parameters: Optional[dict] = None  # 手动指定 schema 时优先级最高

    def to_schema(self) -> dict:
        params = self.parameters or _generate_parameters_schema(self.func)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": params,
            },
        }


class ToolRegistry:
    def __init__(
        self,
        allow_tools: Optional[List[str]] = None,
        allowed_paths: Optional[List[str]] = None,
    ):
        self._tools: Dict[str, Tool] = {}
        self.allow_tools = allow_tools    # 允许的工具名称列表，None 表示不限制
        self.allowed_paths = [Path(p).resolve() for p in (allowed_paths or [])]
    
    def _validate_path(self, path: str) -> bool:
        """校验路径是否在允许的目录内。"""
        if not self.allowed_paths:
            return True  # 未设置限制
        target = Path(path).resolve()
        return any(
            target.is_relative_to(allowed)
            for allowed in self.allowed_paths
        )
    
    def _validate_params(self, tool: Tool, arguments: dict) -> tuple[bool, str]:
        """根据 JSON Schema 校验参数。"""
        schema = tool.parameters or _generate_parameters_schema(tool.func)
        required = schema.get("required", [])
        props = schema.get("properties", {})
        
        for field in required:
            if field not in arguments:
                return False, f"Missing required parameter: {field}"
        
        for field, value in arguments.items():
            if field not in props:
                return False, f"Unknown parameter: {field}"
            # 这里可以加入更详细的类型、范围检查...
        return True, ""

    def register(self, tool: Tool) -> None:
        # 如果设置了白名单，且工具不在其中，则拒绝注册
        if self.allow_tools is not None and tool.name not in self.allow_tools:
            print(f"[Security] Tool '{tool.name}' is not in the allowlist, skipped.")
            return
        self._tools[tool.name] = tool

    def execute(self, tool_name: str, arguments: dict) -> str:
        tool = self._tools.get(tool_name)
        if not tool:
            return f"Error: Tool '{tool_name}' not found."
        try:
            return str(tool.func(**arguments))
        except Exception as e:
            return f"Error executing '{tool_name}': {e}"

    def get_tool_schemas(self) -> list:
        return [t.to_schema() for t in self._tools.values()]

