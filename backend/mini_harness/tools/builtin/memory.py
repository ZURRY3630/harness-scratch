"""长期记忆工具：可写记忆的最小落地。

依赖注入：`LongTermMemory` 由 ToolContext 提供；缺失时不注册（返回 None），
保证"没有记忆后端"的项目也能正常启动。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ...sdk.decorator import build_tool, tool
from ..levels import PermissionLevel
from ..registry import Tool

if TYPE_CHECKING:
    from ..loader import BuiltinFactory, ToolContext


def _memory_save(ctx: "ToolContext") -> Optional[Tool]:
    longterm = ctx.longterm
    if longterm is None:
        return None

    @tool(
        name="memory_save",
        description="把重要事实、用户偏好或决定写入长期记忆，跨会话可用。content 为要记住的一句话。",
        permission=PermissionLevel.ASK_FIRST,   # 可写记忆走权限门控（渐进信任）
    )
    def memory_save(content: str, kind: str = "fact") -> str:
        r = longterm.save(content, kind=kind, source_session=ctx.session_id)
        if r.get("saved"):
            return f"已保存长期记忆 #{r['memory_id']}: {content[:100]}"
        return f"保存失败: {r.get('reason')}"

    return build_tool(memory_save)


def _memory_search(ctx: "ToolContext") -> Optional[Tool]:
    longterm = ctx.longterm
    if longterm is None:
        return None

    @tool(
        name="memory_search",
        description="按关键词检索长期记忆，返回相关条目。",
        permission=PermissionLevel.FULL_TRUST,
    )
    def memory_search(query: str) -> str:
        hits = longterm.recall_for_input(query)
        if not hits:
            return "没有匹配的长期记忆。"
        return "\n".join(f"- [{h['kind']}] {h['content']}" for h in hits)

    return build_tool(memory_search)


# 工具名 -> 工厂
TOOLS: dict[str, "BuiltinFactory"] = {
    "memory_save": _memory_save,
    "memory_search": _memory_search,
}
