# DOC: docs/07-custom-tools.md
"""工具装载器：按配置把内置工具与项目插件工具注册进 ToolRegistry。

两个装载来源：
1. 内置工具（`tools/builtin/`）：框架自带、所有项目共用，按**工具名白名单**装载；
2. 插件工具（`projects/<name>/tools/`）：领域专属，扫描目录下 `.py` 中所有 `@tool` 函数。

设计不变量：
- 本模块是往 ToolRegistry 注册工具的唯一入口（组装层不再自己拼工具列表）；
- 依赖缺失的工具（如没有 LongTermMemory 时的记忆工具）跳过不注册，不产生半个工具；
- 未知工具名、插件文件导入失败一律抛错 —— 配置错误必须显式暴露，不静默降级。
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol

from ..memory.longterm import LongTermMemory
from ..sdk.decorator import build_tool, is_tool
from .builtin import BUILTIN_FACTORIES
from .registry import Tool, ToolRegistry


@dataclass
class ToolContext:
    """工具可用的运行时依赖（内置与插件工具的能力边界）。"""

    longterm: Optional[LongTermMemory] = None
    session_id: str = ""


# 内置工具工厂：上下文 -> Tool；返回 None 表示依赖缺失，不注册
BuiltinFactory = Callable[[ToolContext], Optional[Tool]]


class ToolConfig(Protocol):
    """`ToolLoader.load_all` 需要的最小配置契约（结构化类型）。

    `core.config.ProjectConfig`（P0-1）满足该契约：
        tools = {"builtin": ["echo", ...], "plugins_dir": "projects/<name>/tools"}
    """

    tools: Mapping[str, Any]


class ToolLoader:
    """把配置里的工具声明落地为 ToolRegistry 中的 Tool。"""

    def __init__(
        self,
        registry: ToolRegistry,
        longterm: LongTermMemory | None = None,
        session_id: str = "",
    ) -> None:
        self.registry = registry
        self.context = ToolContext(longterm=longterm, session_id=session_id)

    # ----- 内置工具 -----
    def load_builtin(self, names: list[str]) -> None:
        """按工具名装载内置工具。名字不存在时抛 ValueError。"""
        unknown = [n for n in names if n not in BUILTIN_FACTORIES]
        if unknown:
            raise ValueError(f"未知内置工具: {unknown}（可用: {sorted(BUILTIN_FACTORIES)}）")
        for name in names:
            t = BUILTIN_FACTORIES[name](self.context)
            if t is None:
                continue  # 依赖缺失，跳过
            self.registry.register(t)

    # ----- 插件工具 -----
    def load_from_dir(self, path: str | Path) -> None:
        """扫描目录下所有 `.py`，注册其中被 `@tool` 装饰的函数。目录不存在视为无插件。"""
        directory = Path(path)
        if not directory.is_dir():
            return
        for py_file in sorted(directory.glob("*.py")):
            if py_file.name.startswith("_"):  # 跳过 __init__.py 与私有模块
                continue
            module = _import_module_from_file(py_file)
            for _, func in inspect.getmembers(module, inspect.isfunction):
                if is_tool(func):
                    self.registry.register(build_tool(func))

    # ----- 配置驱动总入口 -----
    def load_all(self, cfg: ToolConfig) -> None:
        """按 ProjectConfig.tools 装载：先内置白名单，再插件目录。"""
        tools_cfg: Mapping[str, Any] = getattr(cfg, "tools", None) or {}
        self.load_builtin(list(tools_cfg.get("builtin") or []))
        plugins_dir = tools_cfg.get("plugins_dir")
        if plugins_dir:
            self.load_from_dir(plugins_dir)


def _import_module_from_file(path: Path) -> Any:
    """按文件路径导入插件模块。

    模块名带路径指纹，避免不同目录下的同名文件（如两个 `tools.py`）互相覆盖。
    """
    digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:8]
    module_name = f"harness_plugin_{path.stem}_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载插件文件: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:  # noqa: BLE001 —— 统一转成 ImportError，附带文件位置
        sys.modules.pop(module_name, None)
        raise ImportError(f"插件文件导入失败 {path}: {e}") from e
    return module
