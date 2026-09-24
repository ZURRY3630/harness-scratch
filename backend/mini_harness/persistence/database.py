# DOC: docs/09-custom-memory.md
"""SQLite 持久化：会话 / 消息 / 摘要 / 长期记忆 / 审批记录。

设计约束：
- 单文件库，WAL 模式，写路径收敛到 Database 一个类
- 表结构最小化，不做 ORM
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ..core.registry import register_memory

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT 'New Chat',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id  TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    tool_calls  TEXT,
    tool_call_id TEXT,
    token_estimate INTEGER NOT NULL DEFAULT 0,
    is_summary  INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);

CREATE TABLE IF NOT EXISTS summaries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    summary     TEXT NOT NULL,
    covers_seq  INTEGER NOT NULL,
    tokens      INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS longterm_memories (
    memory_id   TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'fact',
    keywords    TEXT NOT NULL DEFAULT '',
    source_session TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    call_id     TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    args_hash   TEXT NOT NULL,
    decision    TEXT NOT NULL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_permissions (
    tool_name   TEXT PRIMARY KEY,
    permission  TEXT NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_states (
    slug        TEXT PRIMARY KEY,
    enabled     INTEGER NOT NULL,
    updated_at  REAL NOT NULL
);
"""


@register_memory("sqlite")
class Database:
    """线程安全的 SQLite 访问（FastAPI 线程池 + 引擎协程都会碰到）。"""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def query_all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        rows = self.query_all(sql, params)
        return rows[0] if rows else None

    # ----- sessions -----
    def upsert_session(self, session_id: str, title: str) -> None:
        now = time.time()
        self.execute(
            "INSERT INTO sessions(session_id,title,created_at,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at",
            (session_id, title, now, now),
        )

    def touch_session(self, session_id: str) -> None:
        self.execute("UPDATE sessions SET updated_at=? WHERE session_id=?", (time.time(), session_id))

    def list_sessions(self, limit: int = 50) -> list[dict]:
        return self.query_all(
            "SELECT session_id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )

    def delete_session(self, session_id: str) -> None:
        for sql in (
            "DELETE FROM messages WHERE session_id=?",
            "DELETE FROM summaries WHERE session_id=?",
            "DELETE FROM approvals WHERE session_id=?",
            "DELETE FROM sessions WHERE session_id=?",
        ):
            self.execute(sql, (session_id,))

    # ----- messages -----
    def next_seq(self, session_id: str) -> int:
        row = self.query_one("SELECT COALESCE(MAX(seq),-1)+1 AS n FROM messages WHERE session_id=?", (session_id,))
        return int(row["n"]) if row else 0

    def insert_message(self, session_id: str, m: dict) -> None:
        self.execute(
            "INSERT INTO messages(message_id,session_id,seq,role,content,tool_calls,tool_call_id,"
            "token_estimate,is_summary,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                m["message_id"], session_id, m["seq"], m["role"], m.get("content", ""),
                json.dumps(m["tool_calls"], ensure_ascii=False) if m.get("tool_calls") else None,
                m.get("tool_call_id"), int(m.get("token_estimate", 0)),
                1 if m.get("is_summary") else 0, m.get("created_at", time.time()),
            ),
        )

    def list_messages(self, session_id: str, after_seq: int = -1) -> list[dict]:
        return self.query_all(
            "SELECT * FROM messages WHERE session_id=? AND seq>? ORDER BY seq", (session_id, after_seq)
        )

    def last_seq(self, session_id: str) -> int:
        row = self.query_one("SELECT COALESCE(MAX(seq),-1) AS n FROM messages WHERE session_id=?", (session_id,))
        return int(row["n"]) if row else -1

    # ----- summaries（压缩产物，covers_seq 表示摘要覆盖到哪条 seq）-----
    def insert_summary(self, session_id: str, summary: str, covers_seq: int, tokens: int) -> None:
        self.execute(
            "INSERT INTO summaries(session_id,summary,covers_seq,tokens,created_at) VALUES(?,?,?,?,?)",
            (session_id, summary, covers_seq, tokens, time.time()),
        )

    def latest_summary(self, session_id: str) -> Optional[dict]:
        return self.query_one(
            "SELECT * FROM summaries WHERE session_id=? ORDER BY covers_seq DESC LIMIT 1", (session_id,)
        )

    # ----- longterm memories -----
    def insert_memory(self, memory_id: str, content: str, kind: str, keywords: str, source_session: str) -> None:
        now = time.time()
        self.execute(
            "INSERT INTO longterm_memories(memory_id,content,kind,keywords,source_session,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (memory_id, content, kind, keywords, source_session, now, now),
        )

    def list_memories(self, limit: int = 200) -> list[dict]:
        return self.query_all(
            "SELECT memory_id, content, kind, keywords, created_at FROM longterm_memories "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )

    def search_memories(self, keywords: list[str], top_k: int) -> list[dict]:
        """朴素关键词重合度评分；词表小、零依赖，够用且可替换为向量检索。"""
        rows = self.list_memories(limit=500)
        scored = []
        for r in rows:
            hay = (r["content"] + " " + r["keywords"]).lower()
            score = sum(1 for kw in keywords if kw and kw.lower() in hay)
            if score > 0:
                scored.append((score, r))
        scored.sort(key=lambda x: (-x[0], -x[1]["created_at"]))
        return [r for _, r in scored[:top_k]]

    def delete_memory(self, memory_id: str) -> None:
        self.execute("DELETE FROM longterm_memories WHERE memory_id=?", (memory_id,))

    # ----- approvals（批准记录持久化，重启后仍生效）-----
    def record_approval(self, call_id: str, session_id: str, tool_name: str, args_hash: str, decision: str) -> None:
        self.execute(
            "INSERT OR REPLACE INTO approvals(call_id,session_id,tool_name,args_hash,decision,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (call_id, session_id, tool_name, args_hash, decision, time.time()),
        )

    def get_approval(self, call_id: str) -> Optional[dict]:
        return self.query_one("SELECT * FROM approvals WHERE call_id=?", (call_id,))

    # ----- tool permissions（运行时授权覆盖，立即生效）-----
    def set_tool_permission(self, tool_name: str, permission: str) -> None:
        self.execute(
            "INSERT OR REPLACE INTO tool_permissions(tool_name,permission,updated_at) VALUES(?,?,?)",
            (tool_name, permission, time.time()),
        )

    def get_tool_permission(self, tool_name: str) -> Optional[str]:
        row = self.query_one("SELECT permission FROM tool_permissions WHERE tool_name=?", (tool_name,))
        return row["permission"] if row else None

    def list_tool_permissions(self) -> dict[str, str]:
        return {r["tool_name"]: r["permission"] for r in self.query_all("SELECT tool_name, permission FROM tool_permissions")}

    # ----- skill states（技能启用的运行时覆盖；未覆盖时用 config.yaml 的 skills.enabled）-----
    def set_skill_state(self, slug: str, enabled: bool) -> None:
        self.execute(
            "INSERT OR REPLACE INTO skill_states(slug,enabled,updated_at) VALUES(?,?,?)",
            (slug, 1 if enabled else 0, time.time()),
        )

    def get_skill_state(self, slug: str) -> Optional[bool]:
        row = self.query_one("SELECT enabled FROM skill_states WHERE slug=?", (slug,))
        return bool(row["enabled"]) if row else None

    def list_skill_states(self) -> dict[str, bool]:
        return {r["slug"]: bool(r["enabled"]) for r in self.query_all("SELECT slug, enabled FROM skill_states")}

    def clear_skill_state(self, slug: str) -> bool:
        """清除覆盖；没有覆盖时返回 False。"""
        if self.get_skill_state(slug) is None:
            return False
        self.execute("DELETE FROM skill_states WHERE slug=?", (slug,))
        return True
