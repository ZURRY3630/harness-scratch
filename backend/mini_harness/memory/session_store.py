"""会话账本：消息的内存视图 + SQLite 落盘（状态外置，进程可死可复活）。"""

from __future__ import annotations

import json
import time
from typing import Optional

from ..core.message import Message, Role, ToolCall
from ..persistence.database import Database


class SessionStore:
    """一个会话的消息账本。内存 list 为读缓存，写穿透到 DB。"""

    def __init__(self, db: Database, session_id: str, title: str = "New Chat"):
        self.db = db
        self.session_id = session_id
        db.upsert_session(session_id, title)
        self._messages: list[Message] = []
        self._load()

    def _load(self) -> None:
        self._messages = []
        for row in self.db.list_messages(self.session_id):
            self._messages.append(self._row_to_message(row))

    def reload(self) -> None:
        self._load()

    @staticmethod
    def _row_to_message(row: dict) -> Message:
        calls = None
        if row.get("tool_calls"):
            try:
                calls = [ToolCall(**tc) for tc in json.loads(row["tool_calls"])]
            except (json.JSONDecodeError, TypeError):
                calls = None
        return Message(
            role=Role(row["role"]),
            content=row["content"] or "",
            tool_calls=calls,
            tool_call_id=row.get("tool_call_id"),
            message_id=row["message_id"],
            token_estimate=int(row.get("token_estimate") or 0),
            is_summary=bool(row.get("is_summary")),
            created_at=float(row.get("created_at") or 0),
        )

    # ----- 追加（写穿透）-----
    def _append(self, msg: Message, seq: Optional[int] = None) -> Message:
        msg.created_at = msg.created_at or time.time()
        self._messages.append(msg)
        self.db.insert_message(
            self.session_id,
            {
                "message_id": msg.message_id,
                "seq": self.db.next_seq(self.session_id) if seq is None else seq,
                "role": msg.role.value,
                "content": msg.content,
                "tool_calls": [
                    {"tool_name": tc.tool_name, "arguments": tc.arguments, "call_id": tc.call_id}
                    for tc in msg.tool_calls
                ] if msg.tool_calls else None,
                "tool_call_id": msg.tool_call_id,
                "token_estimate": msg.token_estimate,
                "is_summary": msg.is_summary,
                "created_at": msg.created_at,
            },
        )
        self.db.touch_session(self.session_id)
        return msg

    def add_user(self, text: str, tokens: int = 0) -> Message:
        return self._append(Message(role=Role.USER, content=text, token_estimate=tokens))

    def add_assistant(self, text: str, tokens: int = 0) -> Message:
        return self._append(Message(role=Role.ASSISTANT, content=text, token_estimate=tokens))

    def add_assistant_with_tool_calls(self, text: str, tool_calls: list[ToolCall], tokens: int = 0) -> Message:
        return self._append(
            Message(role=Role.ASSISTANT, content=text or "", tool_calls=tool_calls, token_estimate=tokens)
        )

    def add_tool_result(self, call_id: str, result: str, tokens: int = 0) -> Message:
        return self._append(
            Message(role=Role.TOOL, content=result, tool_call_id=call_id, token_estimate=tokens)
        )

    def add_summary(self, summary_text: str, tokens: int) -> Message:
        return self._append(Message(role=Role.USER, content=summary_text, token_estimate=tokens, is_summary=True))

    def add_summary_at(self, summary_text: str, seq: int, tokens: int) -> Message:
        """压缩专用：摘要消息占据指定 seq（被删旧消息的位置），账本已 reload。"""
        msg = Message(role=Role.USER, content=summary_text, token_estimate=tokens, is_summary=True)
        msg.created_at = time.time()
        self._messages.append(msg)
        self.db.insert_message(
            self.session_id,
            {
                "message_id": msg.message_id, "seq": seq, "role": msg.role.value,
                "content": msg.content, "tool_calls": None, "tool_call_id": None,
                "token_estimate": tokens, "is_summary": True, "created_at": msg.created_at,
            },
        )
        return msg

    # ----- 读取 -----
    @property
    def messages(self) -> list[Message]:
        return self._messages

    def get_pending_tool_calls(self) -> list[ToolCall]:
        """已声明但还没有 tool 结果的调用（对账）。"""
        completed = {m.tool_call_id for m in self._messages if m.role == Role.TOOL and m.tool_call_id}
        pending: list[ToolCall] = []
        for m in self._messages:
            if m.role == Role.ASSISTANT and m.tool_calls:
                for tc in m.tool_calls:
                    if tc.call_id not in completed:
                        pending.append(tc)
        return pending

    def to_openai(self, messages: Optional[list[Message]] = None) -> list[dict]:
        return [m.to_openai() for m in (messages if messages is not None else self._messages)]

    def clear(self) -> None:
        self.db.delete_session(self.session_id)
        self.db.upsert_session(self.session_id, "New Chat")
        self._load()
