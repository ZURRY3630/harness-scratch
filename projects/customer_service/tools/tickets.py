"""售后工单工具（有副作用，逐次审批）。"""

from __future__ import annotations

import itertools
import time

from mini_harness.sdk.decorator import tool
from mini_harness.tools.levels import PermissionLevel

_TICKET_SEQ = itertools.count(1)
_TICKETS: list[dict] = []


@tool(
    name="create_ticket",
    description="创建售后工单，返回工单号。summary 为一行标题，detail 为问题细节。",
    permission=PermissionLevel.ASK_FIRST,   # 有副作用：每次都要人工批准
)
def create_ticket(summary: str, detail: str) -> str:
    ticket_id = f"T{time.strftime('%Y%m%d')}-{next(_TICKET_SEQ):04d}"
    _TICKETS.append({"ticket_id": ticket_id, "summary": summary, "detail": detail})
    return f"已创建工单 {ticket_id}：{summary}（预计 1 个工作日内响应）"


@tool(
    name="list_tickets",
    description="列出本次运行期间已创建的工单，用于确认工单是否创建成功。",
    permission=PermissionLevel.FULL_TRUST,
)
def list_tickets() -> str:
    if not _TICKETS:
        return "暂无工单。"
    return "\n".join(f"- {t['ticket_id']} {t['summary']}" for t in _TICKETS)
