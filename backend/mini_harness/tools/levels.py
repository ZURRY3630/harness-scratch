"""权限级别与决策枚举：独立模块，供 registry 与 permission 双向引用而不循环。"""

from __future__ import annotations

from enum import Enum


class PermissionLevel(str, Enum):
    FULL_TRUST = "full_trust"
    AUTO_WITH_NOTIFICATION = "auto_with_notification"
    ASK_FIRST = "ask_first"
    APPROVE_ALWAYS = "approve_always"
    MANUAL_ONLY = "manual_only"


class Decision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"
    HANDOFF = "handoff"            # 转人工
    ALLOW_NOTIFY = "allow_notify"  # 自动执行 + 通知
