"""工具层测试：Schema 校验、路径边界、权限五级决策。"""

from mini_harness.core.message import ToolCall
from mini_harness.tools.permission import Decision, PermissionGate, PermissionLevel
from mini_harness.tools.registry import Tool, ToolRegistry, validate_arguments


# ---------- validate_arguments ----------
def test_schema_required_and_unknown():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    ok, msg = validate_arguments(schema, {})
    assert not ok and "缺少必填" in msg

    ok, msg = validate_arguments(schema, {"a": "x", "b": 1})
    assert not ok and "未知参数" in msg

    ok, msg = validate_arguments(schema, {"a": "x"})
    assert ok


def test_schema_type_check():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    ok, msg = validate_arguments(schema, {"n": "abc"})
    assert not ok and "类型错误" in msg
    ok, _ = validate_arguments(schema, {"n": 3})
    assert ok
    # bool 不算 integer
    ok, _ = validate_arguments(schema, {"n": True})
    assert not ok


# ---------- registry ----------
def test_register_allowlist():
    reg = ToolRegistry(allow_tools=["echo"])
    reg.register(Tool(name="echo", description="", func=lambda: "x"))
    import pytest
    with pytest.raises(ValueError):
        reg.register(Tool(name="other", description="", func=lambda: "x"))


def test_path_validation(registry):
    ok, _ = registry.validate_and_check("read_file", {"path": "E:/sandbox/a.txt"})
    assert ok
    ok, msg = registry.validate_and_check("read_file", {"path": "C:/Windows/system32/config"})
    assert not ok and "越界" in msg
    ok, msg = registry.validate_and_check("read_file", {"path": "../../etc/passwd"})
    assert not ok


def test_hallucinated_tool(registry):
    ok, msg = registry.validate_and_check("no_such_tool", {})
    assert not ok and "不存在" in msg


# ---------- permission 五级 ----------
def test_full_trust_and_manual_only(gate, registry):
    assert gate.decide("echo", "c1", {"text": "x"}) == Decision.ALLOW
    registry.register(Tool(name="manual", description="", func=lambda: "x",
                           permission=PermissionLevel.MANUAL_ONLY))
    assert gate.decide("manual", "c2", {}) == Decision.HANDOFF


def test_ask_first_flow(gate, registry):
    assert gate.decide("ask_tool", "c1", {"text": "x"}) == Decision.ASK
    gate.approve("c1")
    assert gate.decide("ask_tool", "c1", {"text": "x"}) == Decision.ALLOW


def test_ask_first_persisted_approval(gate, registry, db):
    # 模拟进程重启：新 gate 实例读取持久化批准记录
    gate.approve("c1", tool_name="ask_tool", arguments={"text": "x"})
    gate2 = PermissionGate(registry, approval_store=db)
    assert gate2.decide("ask_tool", "c1", {"text": "x"}) == Decision.ALLOW


def test_approve_always_pattern_memory(gate, registry):
    registry.register(Tool(name="always", description="", func=lambda text="": text,
                           permission=PermissionLevel.APPROVE_ALWAYS))
    assert gate.decide("always", "c1", {"text": "a"}) == Decision.ASK
    gate.approve("c1", tool_name="always", arguments={"text": "a"}, remember=True)
    # 同参数形状的新 call_id 自动放行（渐进信任）
    assert gate.decide("always", "c2", {"text": "b"}) == Decision.ALLOW


def test_auto_with_notification(gate, registry):
    registry.register(Tool(name="auto", description="", func=lambda: "x",
                           permission=PermissionLevel.AUTO_WITH_NOTIFICATION))
    assert gate.decide("auto", "c1", {}) == Decision.ALLOW_NOTIFY


# ---------- 执行沙箱 ----------
def test_execute_timeout(gate):
    def slow():
        import time
        time.sleep(2)

    from mini_harness.tools.registry import Tool
    from mini_harness.tools.permission import PermissionLevel
    gate.registry.register(Tool(name="slow", description="", func=slow,
                                permission=PermissionLevel.FULL_TRUST))
    ok, result, elapsed = gate.execute("slow", {}, timeout_seconds=0.3)
    assert not ok and "TIMEOUT" in result
