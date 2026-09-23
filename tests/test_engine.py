"""引擎集成测试：审批挂起/恢复、流式事件、预算耗尽、持久化恢复（全部用 mock 模型）。"""

from mini_harness.context.assembler import ContextAssembler
from mini_harness.context.budget import Budget
from mini_harness.core.events import EventType
from mini_harness.core.message import ToolCall
from mini_harness.memory.compressor import ContextCompressor
from mini_harness.memory.session_store import SessionStore
from mini_harness.models.provider import ModelResponse
from mini_harness.runtime.engine import RuntimeEngine
from mini_harness.tools.permission import PermissionGate, PermissionLevel
from mini_harness.tools.registry import Tool


def make_engine(db, registry, provider, session_id="t1", max_turns=6, budget=None):
    store = SessionStore(db, session_id)
    gate = PermissionGate(registry, approval_store=db)
    b = budget or Budget()
    comp = ContextCompressor(db, provider, keep_recent=4)
    return RuntimeEngine(
        provider=provider, registry=registry, store=store, gate=gate, budget=b,
        compressor=comp, assembler=ContextAssembler(None),
        system_prompt="测试用系统提示词",
        max_turns=max_turns, tool_timeout=5.0,
    ), store, gate


async def collect(agen):
    return [e async for e in agen]


async def test_simple_answer_flow(db, registry):
    provider = _Mock([ModelResponse(content="你好！")])
    engine, store, _ = make_engine(db, registry, provider)
    events = await collect(engine.run("打个招呼"))

    types = [e.type for e in events]
    assert EventType.RUN_STARTED in types and EventType.RUN_FINISHED in types
    assert any(e.type == EventType.DELTA and e.data["text"] == "你好！" for e in events)
    assert store.messages[-1].content == "你好！"
    # 消息已持久化
    assert len(db.list_messages("t1")) >= 2


async def test_tool_execution_flow(db, registry):
    provider = _Mock([
        ModelResponse(content=None, tool_calls=[ToolCall(tool_name="echo", arguments={"text": "hi"}, call_id="c1")]),
        ModelResponse(content="工具说了 hi"),
    ])
    engine, store, _ = make_engine(db, registry, provider)
    events = await collect(engine.run("调用echo"))

    tool_events = [e for e in events if e.type == EventType.TOOL_EXECUTED]
    assert len(tool_events) == 1 and tool_events[0].data["ok"] is True
    assert store.messages[-1].content == "工具说了 hi"


async def test_approval_suspend_and_resume(db, registry):
    """ASK_FIRST：挂起 -> 批准 -> resume 续跑（核心不变量测试）。"""
    provider = _Mock([
        ModelResponse(content=None, tool_calls=[ToolCall(tool_name="ask_tool", arguments={"text": "x"}, call_id="a1")]),
        ModelResponse(content="审批后完成"),
    ])
    engine, store, gate = make_engine(db, registry, provider)

    # 第一次 run：应挂起在审批
    events = await collect(engine.run("需要审批的操作"))
    apr = [e for e in events if e.type == EventType.APPROVAL_REQUIRED]
    assert len(apr) == 1 and apr[0].data["call_id"] == "a1"
    # 挂起时绝不产生最终回答
    assert not any(e.type == EventType.RUN_FINISHED for e in events)
    # pending 留在账本里
    assert len(store.get_pending_tool_calls()) == 1

    # 批准后续跑
    gate.approve("a1")
    events2 = await collect(engine.resume())
    tool_ev = [e for e in events2 if e.type == EventType.TOOL_EXECUTED]
    assert len(tool_ev) == 1
    assert store.messages[-1].content == "审批后完成"


async def test_invalid_tool_args_handled(db, registry):
    """幻觉参数：校验拦截并回填错误结果，不崩引擎。"""
    provider = _Mock([
        ModelResponse(content=None, tool_calls=[ToolCall(tool_name="echo", arguments={"bogus": 1}, call_id="c1")]),
        ModelResponse(content="已处理错误"),
    ])
    engine, store, _ = make_engine(db, registry, provider)
    events = await collect(engine.run("坏参数"))
    te = [e for e in events if e.type == EventType.TOOL_EXECUTED]
    assert te and te[0].data.get("invalid") is True
    assert store.messages[-1].content == "已处理错误"


async def test_hallucinated_tool_denied(db, registry):
    provider = _Mock([
        ModelResponse(content=None, tool_calls=[ToolCall(tool_name="no_such", arguments={}, call_id="c1")]),
        ModelResponse(content="好"),
    ])
    engine, store, _ = make_engine(db, registry, provider)
    events = await collect(engine.run("调不存在的工具"))
    te = [e for e in events if e.type == EventType.TOOL_EXECUTED]
    assert te and te[0].data["ok"] is False


async def test_max_turns_budget_exhaustion(db, registry):
    """无限要求工具调用：max_turns 耗尽时合法回填 + BUDGET_EXCEEDED。"""
    responses = [ModelResponse(content=None, tool_calls=[ToolCall(tool_name="echo", arguments={"text": str(i)}, call_id=f"c{i}")]) for i in range(20)]
    provider = _Mock(responses)
    engine, store, _ = make_engine(db, registry, provider, max_turns=3)
    events = await collect(engine.run("开始"))
    assert any(e.type == EventType.BUDGET_EXCEEDED for e in events)
    assert any(e.type == EventType.RUN_FINISHED for e in events)
    # 无遗留 pending
    assert store.get_pending_tool_calls() == []


async def test_restart_recovery(db, registry):
    """进程重启模拟：新 SessionStore 从 DB 加载，pending 状态不丢。"""
    provider = _Mock([
        ModelResponse(content=None, tool_calls=[ToolCall(tool_name="ask_tool", arguments={"text": "x"}, call_id="r1")]),
    ])
    engine, store, _ = make_engine(db, registry, provider)
    await collect(engine.run("挂起我"))

    # "重启"：重建 store 与引擎
    store2 = SessionStore(db, "t1")
    assert len(store2.get_pending_tool_calls()) == 1
    assert store2.get_pending_tool_calls()[0].call_id == "r1"


async def test_compression_event_emitted(db, registry):
    """长对话跨多次 run 触发压缩事件。"""
    from mini_harness.memory.session_store import SessionStore as SS
    b = Budget(context_token_budget=800, compress_threshold=0.6)
    provider = _Mock([ModelResponse(content=f"回答{i} " + "z" * 300) for i in range(30)])
    engine, store, _ = make_engine(db, registry, provider, budget=b)
    # 模拟多轮 run：每次 run 积累消息，账本持续增长直到超阈值
    for i in range(6):
        events = await collect(engine.run(f"第{i}个问题，请多聊几句 " + "x" * 200))
    assert any(e.type == EventType.CONTEXT_COMPRESSED for e in events)


async def test_new_input_supersedes_pending_approval(db, registry):
    """挂起期间用户发新消息：自动取消未批准调用，保持消息配对合法（回归：API 400）。"""
    provider = _Mock([
        ModelResponse(content=None, tool_calls=[ToolCall(tool_name="ask_tool", arguments={"text": "x"}, call_id="s1")]),
        ModelResponse(content="新问题已回答"),
    ])
    engine, store, gate = make_engine(db, registry, provider)
    await collect(engine.run("触发审批"))
    assert len(store.get_pending_tool_calls()) == 1

    # 新输入：supersede 挂起调用，序列保持合法
    events = await collect(engine.run("换个话题"))
    sup = [e for e in events if e.data.get("superseded")]
    assert len(sup) == 1
    assert store.get_pending_tool_calls() == []
    assert store.messages[-1].content == "新问题已回答"


class _Mock:
    """带索引的 mock provider。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, messages, tools=None):
        self.calls.append(messages)
        return self.responses.pop(0)

    async def chat_stream(self, messages, tools=None):
        self.calls.append(messages)
        r = self.responses.pop(0)
        if r.content:
            yield {"delta": r.content}
        yield {"response": r}

    async def chat_text(self, messages):
        self.calls.append(messages)
        return "摘要: (测试压缩摘要内容)"
