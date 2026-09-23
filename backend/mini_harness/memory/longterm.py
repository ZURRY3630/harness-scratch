# DOC: docs/09-custom-memory.md
"""长期记忆库：跨会话持久化的用户偏好 / 项目事实。

- 存储：SQLite longterm_memories 表
- 检索：CJK 二元组 + 英文单词的重合度评分（零依赖，接口稳定，后续可替换为向量检索）
- 写入：由 agent 通过工具（memory_save）触发，生产中可加权限门控
"""

from __future__ import annotations

import re
import uuid

from ..core.events import EventType, ev
from ..core.message import Message
from ..persistence.database import Database
from .session_store import SessionStore

_STOP = {"的话", "一个", "什么", "这个", "那个", "就是", "但是", "所以", "如果", "我们", "你们", "他们"}


def _keywords(text: str) -> list[str]:
    """CJK 二元组（滑动窗口）+ 英文单词，去重后截断。二元组保证短查询也能命中。"""
    out: list[str] = []
    for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]+", text):
        out.append(w.lower())
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            out.append(run)
        for i in range(len(run) - 1):
            out.append(run[i : i + 2])
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t not in seen and t not in _STOP:
            seen.add(t)
            uniq.append(t)
    return uniq[:24]


class LongTermMemory:
    def __init__(self, db: Database, top_k: int = 3):
        self.db = db
        self.top_k = top_k

    # ----- 写 -----
    def save(self, content: str, kind: str = "fact", source_session: str = "") -> dict:
        content = content.strip()
        if not content:
            return {"saved": False, "reason": "empty"}
        memory_id = uuid.uuid4().hex[:12]
        kws = " ".join(_keywords(content))
        self.db.insert_memory(memory_id, content, kind, kws, source_session)
        return {"saved": True, "memory_id": memory_id, "keywords": kws}

    def delete(self, memory_id: str) -> bool:
        self.db.delete_memory(memory_id)
        return True

    def list(self, limit: int = 100) -> list[dict]:
        return self.db.list_memories(limit)

    # ----- 读 -----
    def recall_for_input(self, user_text: str) -> list[dict]:
        """按用户输入检索相关记忆，供上下文组装。"""
        return self.db.search_memories(_keywords(user_text), self.top_k)

    def recall_from_history(self, messages: list[Message]) -> list[dict]:
        """无当前输入时（续跑场景），用最近用户消息检索。"""
        for m in reversed(messages):
            if m.role.value == "user" and not m.is_summary:
                return self.recall_for_input(m.content)
        return []

    def event_saved(self, session_id: str, info: dict):
        return ev(EventType.MEMORY_SAVED, session_id, **info)
