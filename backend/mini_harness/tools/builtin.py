"""内置工具：演示工具 + 长期记忆读写工具（记忆工具是"可写记忆"的最小落地）。"""

from __future__ import annotations

from datetime import datetime, timezone

from ..memory.longterm import LongTermMemory
from .permission import PermissionLevel
from .registry import Tool, ToolRegistry


def build_builtin_tools(longterm: LongTermMemory | None = None, session_id: str = "") -> list[Tool]:
    tools: list[Tool] = [
        Tool(
            name="echo",
            description="原样返回输入文本，用于连通性测试。",
            func=lambda text: f"Echo: {text}",
            permission=PermissionLevel.FULL_TRUST,
        ),
        Tool(
            name="get_time",
            description="返回当前 UTC 时间（ISO 格式）。",
            func=lambda: datetime.now(timezone.utc).isoformat(),
            permission=PermissionLevel.AUTO_WITH_NOTIFICATION,
        ),
        Tool(
            name="simulate_delete_file",
            description="模拟删除文件（演示审批流，不真删）。",
            func=lambda path: f"[SIMULATED] Deleted: {path}",
            permission=PermissionLevel.ASK_FIRST,
            path_arg="path",
        ),
    ]
    if longterm is not None:
        tools.append(_make_memory_save_tool(longterm, session_id))
        tools.append(_make_memory_search_tool(longterm))
    return tools


def _make_memory_save_tool(longterm: LongTermMemory, session_id: str) -> Tool:
    def memory_save(content: str, kind: str = "fact") -> str:
        r = longterm.save(content, kind=kind, source_session=session_id)
        if r.get("saved"):
            return f"已保存长期记忆 #{r['memory_id']}: {content[:100]}"
        return f"保存失败: {r.get('reason')}"

    return Tool(
        name="memory_save",
        description="把重要事实、用户偏好或决定写入长期记忆，跨会话可用。content 为要记住的一句话。",
        func=memory_save,
        permission=PermissionLevel.ASK_FIRST,   # 可写记忆走权限门控（渐进信任）
    )


def _make_memory_search_tool(longterm: LongTermMemory) -> Tool:
    def memory_search(query: str) -> str:
        hits = longterm.recall_for_input(query)
        if not hits:
            return "没有匹配的长期记忆。"
        return "\n".join(f"- [{h['kind']}] {h['content']}" for h in hits)

    return Tool(
        name="memory_search",
        description="按关键词检索长期记忆，返回相关条目。",
        func=memory_search,
        permission=PermissionLevel.FULL_TRUST,
    )
