"""组件注册表（P1-1）：按「类别 + 类型名」反射构造组件，换实现不改内核。

实现侧（在类定义处加装饰器）：

    @register_provider("openai")
    class OpenAIProvider(BaseModelProvider): ...

组装侧：

    ComponentRegistry.build("provider", cfg.provider["type"], **kwargs)

类别：`provider`（模型供应商）/ `memory`（记忆后端）/ `gate`（权限门控）。
本期只注册三个默认实现：openai / sqlite / interactive，够 build_engine 用。
"""

from __future__ import annotations

import importlib
from typing import Any, Callable

# 类别 -> {类型名: 类}
_REGISTRY: dict[str, dict[str, type]] = {"provider": {}, "memory": {}, "gate": {}}

# 默认实现所在模块；惰性导入，避免 core 层出现对上层的静态依赖
_DEFAULT_MODULES: tuple[str, ...] = (
    "mini_harness.models.openai_provider",
    "mini_harness.persistence.database",
    "mini_harness.tools.permission",
)
_defaults_loaded = False


def register(category: str, name: str) -> Callable[[type], type]:
    """把实现类注册到某类别的某个类型名（重复注册即覆盖，保持幂等）。"""

    def decorate(cls: type) -> type:
        _REGISTRY.setdefault(category, {})[name] = cls
        return cls

    return decorate


def register_provider(name: str) -> Callable[[type], type]:
    """注册模型供应商实现。"""
    return register("provider", name)


def register_memory(name: str) -> Callable[[type], type]:
    """注册记忆后端实现。"""
    return register("memory", name)


def register_gate(name: str) -> Callable[[type], type]:
    """注册权限门控实现。"""
    return register("gate", name)


class ComponentRegistry:
    """反射构造入口（无实例状态，全部静态方法）。"""

    @staticmethod
    def build(category: str, type_name: str, **kwargs: Any) -> Any:
        """构造组件实例。

        类别或类型名未知时抛 ValueError（配置错误要显式暴露，不静默降级为默认实现）。
        """
        _load_defaults()
        if category not in _REGISTRY:
            raise ValueError(f"未知组件类别: {category}（可用: {sorted(_REGISTRY)}）")
        impl = _REGISTRY[category].get(type_name)
        if impl is None:
            raise ValueError(f"未知 {category} 类型: {type_name}（可用: {sorted(_REGISTRY[category])}）")
        return impl(**kwargs)

    @staticmethod
    def available(category: str) -> list[str]:
        """某类别下已注册的类型名（排障与文档用）。"""
        _load_defaults()
        return sorted(_REGISTRY.get(category, {}))


def _load_defaults() -> None:
    """导入默认实现模块一次，触发其装饰器注册。"""
    global _defaults_loaded
    if _defaults_loaded:
        return
    for module_name in _DEFAULT_MODULES:
        importlib.import_module(module_name)
    _defaults_loaded = True
