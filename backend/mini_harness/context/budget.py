"""Token 预算管理：估算、上限检查、用量统计。

设计原则：预算是"估算 + 硬顶"，不追求分词级精确；所有进入上下文的文本都过这里。
"""

from __future__ import annotations

from dataclasses import dataclass, field


def estimate_tokens(text: str) -> int:
    """粗估 token：CJK 字符约 1 token/字，其他约 4 字符/token。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return max(1, cjk + other // 4)


@dataclass
class Usage:
    """一次 run 的用量记录。"""

    prompt_tokens: int = 0        # 模型实际回报的 prompt tokens（可得时）
    completion_tokens: int = 0
    estimated_context_tokens: int = 0   # 本地估算的上下文大小
    llm_calls: int = 0
    tool_calls: int = 0
    compressions: int = 0
    cache_hits: int = 0           # provider 层统计：重复前缀命中次数


@dataclass
class Budget:
    """上下文预算：软阈值触发压缩，硬顶用于组装时截断。"""

    context_token_budget: int = 24000
    reserve_output_tokens: int = 4096
    compress_threshold: float = 0.8
    usage: Usage = field(default_factory=Usage)

    @property
    def hard_cap(self) -> int:
        """给模型输入的硬上限 = 预算 - 预留输出。"""
        return max(1024, self.context_token_budget - self.reserve_output_tokens)

    def soft_cap(self) -> int:
        return int(self.context_token_budget * self.compress_threshold)

    def account_context(self, tokens: int) -> None:
        self.usage.estimated_context_tokens = tokens

    def account_llm(self, prompt: int = 0, completion: int = 0) -> None:
        self.usage.llm_calls += 1
        self.usage.prompt_tokens += prompt
        self.usage.completion_tokens += completion

    def account_tool(self) -> None:
        self.usage.tool_calls += 1

    def account_compression(self) -> None:
        self.usage.compressions += 1

    def account_cache_hit(self) -> None:
        self.usage.cache_hits += 1

    def snapshot(self) -> dict:
        return {
            "budget": self.context_token_budget,
            "hard_cap": self.hard_cap,
            "estimated_context_tokens": self.usage.estimated_context_tokens,
            "prompt_tokens": self.usage.prompt_tokens,
            "completion_tokens": self.usage.completion_tokens,
            "llm_calls": self.usage.llm_calls,
            "tool_calls": self.usage.tool_calls,
            "compressions": self.usage.compressions,
            "cache_hits": self.usage.cache_hits,
        }
