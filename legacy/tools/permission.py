from __future__ import annotations

from enum import Enum
from typing import Set, TYPE_CHECKING

if TYPE_CHECKING:
    from tools.registry import ToolRegistry



class PermissionLevel(str, Enum):
    FULL_TRUST = "full_trust"  # 全信，不提示确认
    AUTO_WITH_NOTIFICATION = "auto_with_notification"  # 自动执行，提示用户确认
    ASK_FIRST = "ask_first"  # 先提示用户确认，再执行
    APPROVE_ALWAYS = "approve_always"  # 总是自动执行，不提示用户确认
    MANUAL_ONLY = "manual_only"  # 只有手动确认后才执行

class Decision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PermissionGate:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self.approved_call_ids: Set[str] = set()   # 被用户批准的 call_id

    def decide(self, tool_name: str, call_id: str) -> Decision:
        tool = self.registry._tools.get(tool_name)
        if not tool:
            return Decision.DENY

        # level = getattr(tool, "permission", PermissionLevel.FULL_TRUST)
        level = tool.permission or PermissionLevel.ASK_FIRST
        # if level is None:
        #     level = PermissionLevel.FULL_TRUST
        # if level == PermissionLevel.FULL_TRUST:
        #     return Decision.ALLOW
        # if level == PermissionLevel.MANUAL_ONLY:    
        #     return Decision.DENY
        # if level == PermissionLevel.ASK_FIRST:
        #     return Decision.ALLOW if call_id in self.approved_call_ids else Decision.ASK
        # return Decision.DENY
        if level == PermissionLevel.FULL_TRUST:
            return Decision.ALLOW
        if level in (PermissionLevel.ASK_FIRST, PermissionLevel.AUTO_WITH_NOTIFICATION, PermissionLevel.APPROVE_ALWAYS):
            return Decision.ALLOW if call_id in self.approved_call_ids else Decision.ASK
        if level == PermissionLevel.MANUAL_ONLY:
            return Decision.DENY
        return Decision.DENY

    def approve(self, call_id: str) -> None:
        self.approved_call_ids.add(call_id)

    def execute(self, tool_name: str, arguments: dict):
        tool = self.registry._tools.get(tool_name)
        if not tool:
            return False, f"Tool '{tool_name}' not found."
        try:
            return True, str(tool.func(**arguments))
        except Exception as e:
            return False, f"Tool execution error: {e}"