"""插件作者 SDK：编写工具与钩子只需依赖本包（对内核其余部分零依赖）。"""

from __future__ import annotations

from .decorator import ToolSpec, build_tool, get_spec, is_tool, tool

__all__ = ["tool", "ToolSpec", "build_tool", "get_spec", "is_tool"]
