"""上下文压缩器：接近预算时把旧轮次摘要化（对齐 Claude Code 的 compact 机制）。

策略（保守、可预测）：
- 触发：估算 token >= context_token_budget * compress_threshold
- 分段：摘要段 = 除最近 keep_recent 条外的全部；保护段 = 最近消息 + 所有未回填的 tool 调用链
- 产物：摘要写入 summaries 表（可追溯）+ 以 is_summary 消息占据被删消息的 seq 位置
- 不变量：绝不破坏 tool 消息配对；保留段不以孤儿 tool 结果开头
"""

from __future__ import annotations

import json

from ..context.budget import estimate_tokens
from ..core.message import Role
from ..models.provider import BaseModelProvider
from ..persistence.database import Database
from .session_store import SessionStore

_COMPRESS_SYSTEM = (
    "你是对话摘要器。把给定的对话历史压缩成一份简洁摘要，供 AI 助手继续工作使用。"
    "要求：1) 保留用户的全部目标、约束和偏好；2) 保留已完成的工具调用及其关键结果（文件、命令、数值）；"
    "3) 保留未完成事项；4) 用紧凑的条目式中文书写，不要客套话。"
)


class ContextCompressor:
    def __init__(self, db: Database, provider: BaseModelProvider, keep_recent: int = 8):
        self.db = db
        self.provider = provider
        self.keep_recent = keep_recent

    def should_compress(self, store: SessionStore, budget_tokens: int, threshold: float) -> bool:
        total = sum(m.token_estimate for m in store.messages)
        return total >= int(budget_tokens * threshold)

    def _split(self, store: SessionStore) -> tuple[list, list]:
        """返回 (to_summarize, to_keep)。保护 pending 工具调用链与最近消息。"""
        messages = store.messages
        if len(messages) <= self.keep_recent:
            return [], list(messages)

        pending = {tc.call_id for tc in store.get_pending_tool_calls()}
        cut = len(messages) - self.keep_recent
        # 若保留段外存在 pending tool_call 的源头 assistant 消息，从那里开始保护
        for i in range(cut):
            m = messages[i]
            if m.role == Role.ASSISTANT and m.tool_calls and any(tc.call_id in pending for tc in m.tool_calls):
                cut = i
                break
        to_summarize = messages[:cut]
        to_keep = messages[cut:]
        # 保留段不能以孤儿 tool 结果开头（其声明的 assistant 已在摘要段）
        while to_keep and to_keep[0].role == Role.TOOL:
            to_summarize.append(to_keep.pop(0))
        return to_summarize, to_keep

    async def compress(self, store: SessionStore, budget_tokens: int, threshold: float) -> dict:
        if not self.should_compress(store, budget_tokens, threshold):
            return {"compressed": False}

        to_summarize, to_keep = self._split(store)
        if len(to_summarize) < 2:
            return {"compressed": False}

        summary_text = await self.provider.chat_text(
            [
                {"role": "system", "content": _COMPRESS_SYSTEM},
                {"role": "user", "content": _render_transcript(to_summarize)},
            ]
        )
        summary_tokens = estimate_tokens(summary_text)
        covers_seq = self._covers_seq(store, [m.message_id for m in to_summarize])

        # 落盘：摘要入 summaries 表；被覆盖的消息行删除；摘要消息顶替到 covers_seq 位置
        self.db.insert_summary(store.session_id, summary_text, covers_seq, summary_tokens)
        self.db.execute("DELETE FROM messages WHERE session_id=? AND seq<=?", (store.session_id, covers_seq))
        store.add_summary_at(summary_text, seq=covers_seq, tokens=summary_tokens)

        return {
            "compressed": True,
            "summarized_messages": len(to_summarize),
            "kept_messages": len(to_keep),
            "summary_tokens": summary_tokens,
        }

    def _covers_seq(self, store: SessionStore, message_ids: list[str]) -> int:
        """被摘要覆盖的最大 seq。"""
        if not message_ids:
            return store.db.last_seq(store.session_id)
        placeholders = ",".join("?" * len(message_ids))
        row = store.db.query_one(
            f"SELECT MAX(seq) AS m FROM messages WHERE session_id=? AND message_id IN ({placeholders})",
            (store.session_id, *message_ids),
        )
        if row and row["m"] is not None:
            return int(row["m"])
        return store.db.last_seq(store.session_id)


def _render_transcript(messages: list) -> str:
    lines = []
    for m in messages:
        if m.role == Role.TOOL:
            lines.append(f"[tool:{m.tool_call_id}] {m.content[:500]}")
        elif m.role == Role.ASSISTANT and m.tool_calls:
            calls = "; ".join(f"{tc.tool_name}({json.dumps(tc.arguments, ensure_ascii=False)})" for tc in m.tool_calls)
            lines.append(f"[assistant 调用工具: {calls}]" + (m.content or ""))
        else:
            tag = "摘要" if m.is_summary else m.role.value
            lines.append(f"[{tag}] {m.content}")
    return "\n".join(lines)
