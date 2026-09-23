"""预算 / 压缩 / 组装 / 长期记忆测试。"""

from mini_harness.context.budget import estimate_tokens
from mini_harness.core.message import ToolCall
from mini_harness.memory.compressor import ContextCompressor


# ---------- 估算 ----------
def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1          # 4 字符 ≈ 1 token
    assert estimate_tokens("你好世界") == 4       # CJK 1:1
    assert estimate_tokens("你好world") == 3      # 2 CJK + 5 ASCII÷4 = 3


class MockResp:
    """压缩器专用：chat_text 返回固定摘要。"""

    async def chat_text(self, messages):
        return "摘要：用户问了6个问题，已回答；最后调用了 echo 工具。"


async def test_compress_triggers_and_preserves_pairing(db, store, longterm, budget):
    comp = ContextCompressor(db, MockResp(), keep_recent=3)

    # 构造：12 条大消息（超阈值）+ 尾部一条已完成的工具调用链
    for i in range(6):
        store.add_user(f"问题{i} " + "x" * 400, tokens=400)
        store.add_assistant(f"回答{i} " + "y" * 400, tokens=400)
    store.add_assistant_with_tool_calls("", [ToolCall(tool_name="echo", arguments={}, call_id="c9")], tokens=10)
    store.add_tool_result("c9", "done", tokens=5)
    store.add_user("最新问题", tokens=10)

    info = await comp.compress(store, budget_tokens=1000, threshold=0.5)
    assert info["compressed"] is True
    # 摘要消息在账本里
    summaries = [m for m in store.messages if m.is_summary]
    assert len(summaries) == 1
    # 完整的 tool 配对保留在保留段
    assert any(m.role.value == "tool" for m in store.messages)
    # 摘要被持久化
    assert db.latest_summary("test_session") is not None


async def test_compress_not_triggered_below_threshold(db, store, budget):
    comp = ContextCompressor(db, MockResp(), keep_recent=3)
    store.add_user("hi", tokens=10)
    info = await comp.compress(store, budget_tokens=24000, threshold=0.8)
    assert info["compressed"] is False


async def test_compress_keeps_pending_chain(db, store, budget):
    """pending 工具调用链不能被摘要段切断。"""
    comp = ContextCompressor(db, MockResp(), keep_recent=3)
    for i in range(6):
        store.add_user(f"q{i}", tokens=300)
        store.add_assistant(f"a{i}", tokens=300)
    # 一条 pending（声明了但无结果）在靠前位置
    store.add_assistant_with_tool_calls("", [ToolCall(tool_name="echo", arguments={}, call_id="pend1")], tokens=10)
    store.add_user("new", tokens=10)

    info = await comp.compress(store, budget_tokens=500, threshold=0.5)
    if info.get("compressed"):
        # pend1 的声明消息必须还在账本里（未被摘要吞掉）
        assert any(
            m.tool_calls and any(tc.call_id == "pend1" for tc in m.tool_calls)
            for m in store.messages
        )


# ---------- 硬顶裁剪 ----------
def test_assembler_trim_respects_pairing(db, store, budget):
    from mini_harness.context.assembler import ContextAssembler
    asm = ContextAssembler(None)
    small = type(budget)(context_token_budget=100, reserve_output_tokens=10)
    for i in range(20):
        store.add_user(f"u{i}", tokens=50)
        store.add_assistant(f"a{i}", tokens=50)
    ctx = asm.assemble(store, small, system_prompt="sys")
    assert ctx.total_tokens <= small.hard_cap + 60  # 允许最后一条溢出
    assert not (ctx.messages and ctx.messages[0].get("role") == "tool")


# ---------- 长期记忆 ----------
def test_longterm_save_and_recall(longterm):
    longterm.save("用户偏好使用 Python 编写代码", kind="preference")
    longterm.save("项目部署在 Windows 服务器", kind="fact")
    hits = longterm.recall_for_input("用户的编程语言偏好")
    assert len(hits) >= 1


def test_longterm_empty_recall(longterm):
    assert longterm.recall_for_input("完全不相关的内容xyz") == []
