"""上下文组装器：把 账本 + 长期记忆 + system prompt 组装成缓存友好的模型输入。

缓存友好（Prompt 缓存）三原则：
1. 前缀稳定 —— system prompt 逐字稳定（时间戳等易变信息不进 system）
2. 只追加 —— 历史消息顺序不变、不重写；压缩时旧消息整体摘除并替换为摘要
3. 动态信息后置 —— 长期记忆等易变内容注入到历史尾部、本轮输入之前
   （注意：注入条目随检索结果变化，会使其后的内容缓存失效，因此必须放在最尾部）
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.message import Message, Role
from ..memory.longterm import LongTermMemory
from ..memory.session_store import SessionStore
from .budget import Budget, estimate_tokens


@dataclass
class AssembledContext:
    messages: list[dict]          # OpenAI 格式
    total_tokens: int
    stable_prefix_tokens: int     # 稳定前缀规模（缓存命中基准）
    longterm_used: int            # 注入的长期记忆条数


class ContextAssembler:
    def __init__(self, longterm: LongTermMemory | None):
        self.longterm = longterm

    def assemble(
        self,
        store: SessionStore,
        budget: Budget,
        system_prompt: str = "",
        recall_query: str = "",
    ) -> AssembledContext:
        stable_tokens = 0
        out: list[Message] = []

        # 1) 稳定前缀：system
        if system_prompt:
            out.append(Message(role=Role.SYSTEM, content=system_prompt))
            stable_tokens += estimate_tokens(system_prompt)

        # 2) 历史：硬顶防线（正常路径由压缩器保证不超限）
        history = list(store.messages)
        stable_tokens += sum(m.token_estimate for m in history)
        if stable_tokens > budget.hard_cap:
            history = _trim_oldest(history, budget.hard_cap - (stable_tokens - sum(m.token_estimate for m in history)))
            stable_tokens = (stable_tokens - sum(m.token_estimate for m in list(store.messages))) + sum(
                m.token_estimate for m in history
            )

        # 3) 动态层：长期记忆注入到历史尾部（本轮 user 输入之前）
        longterm_used = 0
        if self.longterm is not None and recall_query:
            memories = self.longterm.recall_for_input(recall_query)
            if memories:
                block = "\n".join(f"- [{m['kind']}] {m['content']}" for m in memories)
                memo = Message(role=Role.SYSTEM, content=f"[长期记忆检索结果 · 供参考]\n{block}")
                history.append(memo)
                longterm_used = len(memories)

        all_msgs = out + history
        messages = [m.to_openai() for m in all_msgs]
        total = sum(m.token_estimate for m in all_msgs)
        budget.account_context(total)
        return AssembledContext(messages=messages, total_tokens=total,
                                stable_prefix_tokens=stable_tokens, longterm_used=longterm_used)


def _trim_oldest(messages: list[Message], token_cap: int) -> list[Message]:
    """从最老开始丢，保持 tool 配对完整。"""
    acc = 0
    keep_from = 0
    for i in range(len(messages) - 1, -1, -1):
        acc += messages[i].token_estimate or estimate_tokens(messages[i].content)
        if acc > token_cap:
            keep_from = i + 1
            break
    kept = messages[keep_from:]
    declared: set[str] = set()
    for m in kept:
        if m.role == Role.ASSISTANT and m.tool_calls:
            declared.update(tc.call_id for tc in m.tool_calls)
    while kept and kept[0].role == Role.TOOL and kept[0].tool_call_id not in declared:
        kept.pop(0)
    return kept
