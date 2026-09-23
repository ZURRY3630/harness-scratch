"""测试公共设施：内存 SQLite、mock 模型供应商。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from mini_harness.context.assembler import ContextAssembler  # noqa: E402
from mini_harness.context.budget import Budget  # noqa: E402
from mini_harness.memory.compressor import ContextCompressor  # noqa: E402
from mini_harness.memory.longterm import LongTermMemory  # noqa: E402
from mini_harness.memory.session_store import SessionStore  # noqa: E402
from mini_harness.models.provider import BaseModelProvider, ModelResponse  # noqa: E402
from mini_harness.persistence.database import Database  # noqa: E402
from mini_harness.tools.permission import Decision, PermissionGate, PermissionLevel  # noqa: E402
from mini_harness.tools.registry import Tool, ToolRegistry  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture()
def store(db):
    return SessionStore(db, "test_session")


@pytest.fixture()
def longterm(db):
    return LongTermMemory(db, top_k=3)


@pytest.fixture()
def budget():
    return Budget(context_token_budget=24000, reserve_output_tokens=4096, compress_threshold=0.8)


@pytest.fixture()
def registry():
    reg = ToolRegistry(allowed_paths=["E:/sandbox"])

    def read_file(path: str) -> str:
        return f"content of {path}"

    def echo(text: str) -> str:
        return f"Echo: {text}"

    reg.register(Tool(name="read_file", description="read", func=read_file,
                      permission=PermissionLevel.FULL_TRUST, path_arg="path"))
    reg.register(Tool(name="echo", description="echo", func=echo,
                      permission=PermissionLevel.FULL_TRUST))
    reg.register(Tool(name="ask_tool", description="needs approval", func=echo,
                      permission=PermissionLevel.ASK_FIRST))
    return reg


class MockProvider(BaseModelProvider):
    """脚本化响应队列；可注入流式分片。"""

    def __init__(self, responses: list[ModelResponse], deltas: dict[int, list[str]] | None = None):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []
        self.deltas = deltas or {}

    async def chat(self, messages, tools=None):
        self.calls.append(messages)
        return self.responses.pop(0)

    async def chat_stream(self, messages, tools=None):
        self.calls.append(messages)
        idx = len(self.calls) - 1
        resp = self.responses.pop(0)
        for d in self.deltas.get(idx, [resp.content or ""]):
            yield {"delta": d}
        yield {"response": resp}


@pytest.fixture()
def gate(registry, db):
    return PermissionGate(registry, approval_store=db)
