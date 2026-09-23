"""工具管理测试：权限覆盖持久化 + 生效级别解析 + API 端到端。"""

import pytest
from fastapi.testclient import TestClient

from mini_harness.core.message import ToolCall
from mini_harness.models.provider import ModelResponse
from mini_harness.tools.levels import Decision, PermissionLevel
from mini_harness.tools.registry import Tool


# ---------- 权限覆盖解析 ----------
def test_override_takes_precedence(gate, registry, db):
    """DB 覆盖 > 工具声明默认：echo 默认 FULL_TRUST，覆盖为 ASK_FIRST 后应要求审批。"""
    assert gate.decide("echo", "c1", {"text": "x"}) == Decision.ALLOW
    db.set_tool_permission("echo", "ask_first")
    assert gate.effective_level("echo") == PermissionLevel.ASK_FIRST
    assert gate.decide("echo", "c2", {"text": "x"}) == Decision.ASK


def test_override_elevate_to_full_trust(gate, registry, db):
    """反向：ask_tool 默认 ASK_FIRST，覆盖为 full_trust 后免审批。"""
    assert gate.decide("ask_tool", "c1", {"text": "x"}) == Decision.ASK
    db.set_tool_permission("ask_tool", "full_trust")
    assert gate.decide("ask_tool", "c2", {"text": "x"}) == Decision.ALLOW


def test_override_manual_only_blocks(gate, registry, db):
    """覆盖为 manual_only -> HANDOFF 转人工。"""
    db.set_tool_permission("echo", "manual_only")
    assert gate.decide("echo", "c1", {"text": "x"}) == Decision.HANDOFF


def test_invalid_override_falls_back(gate, registry, db):
    """DB 里有脏数据（非法级别字符串）时回退到声明默认，不崩。"""
    db.set_tool_permission("echo", "bogus_level")
    assert gate.effective_level("echo") == PermissionLevel.FULL_TRUST


def test_clear_override(gate, registry, db):
    db.set_tool_permission("echo", "ask_first")
    db.execute("DELETE FROM tool_permissions WHERE tool_name=?", ("echo",))
    assert gate.effective_level("echo") == PermissionLevel.FULL_TRUST


def test_list_tool_permissions(db):
    db.set_tool_permission("echo", "ask_first")
    db.set_tool_permission("get_time", "full_trust")
    m = db.list_tool_permissions()
    assert m == {"echo": "ask_first", "get_time": "full_trust"}


# ---------- API 端到端 ----------
@pytest.fixture()
def client(tmp_path, monkeypatch):
    """独立 TestClient：临时 DB + 独立 engine 容器。"""
    import mini_harness.api.routes as routes

    monkeypatch.setattr(routes, "_db", None)
    monkeypatch.setattr(routes, "_longterm", None)
    monkeypatch.setattr(routes, "_engines", {})

    from mini_harness.main import app
    return TestClient(app)


def test_api_list_tools(client):
    r = client.get("/api/tools")
    assert r.status_code == 200
    tools = {t["name"]: t for t in r.json()}
    assert "echo" in tools and "simulate_delete_file" in tools
    assert tools["echo"]["effective"] == "full_trust"
    assert tools["echo"]["override"] is None
    assert tools["simulate_delete_file"]["path_guard"] is True


def test_api_set_and_effect(client):
    # 修改权限
    r = client.put("/api/tools/echo/permission", json={"permission": "ask_first"})
    assert r.status_code == 200 and r.json()["persisted"] is True
    # 列表反映 override + effective
    tools = {t["name"]: t for t in client.get("/api/tools").json()}
    assert tools["echo"]["override"] == "ask_first"
    assert tools["echo"]["effective"] == "ask_first"
    # 非法级别 400
    assert client.put("/api/tools/echo/permission", json={"permission": "bogus"}).status_code == 400
    # 不存在 404
    assert client.put("/api/tools/no_such/permission", json={"permission": "ask_first"}).status_code == 404


def test_api_clear_override(client):
    client.put("/api/tools/echo/permission", json={"permission": "ask_first"})
    assert client.delete("/api/tools/echo/permission").status_code == 200
    tools = {t["name"]: t for t in client.get("/api/tools").json()}
    assert tools["echo"]["override"] is None
    assert tools["echo"]["effective"] == "full_trust"
    # 重复清除 404
    assert client.delete("/api/tools/echo/permission").status_code == 404


def test_api_override_changes_chat_behavior(client):
    """端到端：echo 提权为 full_trust 后，聊天中调用免审批直接执行。"""
    # echo 覆盖为 ask_first 时，会触发审批挂起
    client.put("/api/tools/echo/permission", json={"permission": "ask_first"})
    s = client.post("/api/sessions", json={}).json()

    # 直接用 SSE 消费（TestClient 流式）：用 stream 方式拿事件
    events = []
    with client.stream("POST", "/api/chat", json={"session_id": s["session_id"], "message": "调用 echo 说 hi"}) as r:
        for line in r.iter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                import json as _json
                events.append(_json.loads(line[6:]))
    assert any(e["type"] == "approval_required" for e in events), "覆盖为 ask_first 后应挂起"

    # 清除覆盖，重新发起：应直接执行
    client.delete("/api/tools/echo/permission")
    events2 = []
    with client.stream("POST", "/api/chat", json={"session_id": s["session_id"], "message": "再调用一次 echo"}) as r:
        for line in r.iter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                import json as _json
                events2.append(_json.loads(line[6:]))
    tool_evs = [e for e in events2 if e["type"] == "tool_executed" and e["data"].get("tool") == "echo"]
    assert tool_evs and tool_evs[0]["data"]["ok"] is True, "清除覆盖后应直接执行成功"
