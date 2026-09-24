"""内置工具包：框架自带、所有项目共用的通用能力。

新增内置工具 = 在本目录新增/修改模块，并在该模块的 `TOOLS` 字典里登记名字；
装载由 `tools.loader.ToolLoader.load_builtin()` 按配置里的工具名白名单完成。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import memory, skills, system

if TYPE_CHECKING:
    from ..loader import BuiltinFactory

# 顺序即默认注册顺序：system 在前，记忆工具其次，技能工具最后
BUILTIN_FACTORIES: dict[str, "BuiltinFactory"] = {**system.TOOLS, **memory.TOOLS, **skills.TOOLS}

__all__ = ["BUILTIN_FACTORIES", "memory", "skills", "system"]
