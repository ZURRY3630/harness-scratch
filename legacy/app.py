import asyncio
import json
from datetime import datetime, timezone

import streamlit as st
from dotenv import load_dotenv

from memory.storage import SimpleMemory
from models.openai_provider import OpenAIProvider
from runtime.engine import RuntimeEngine
from tools.permission import PermissionGate
from tools.permission import PermissionLevel, Decision
from tools.registry import ToolRegistry, Tool

load_dotenv()
st.set_page_config(page_title="Mini Harness", page_icon="🤖", layout="wide")


# ---------- 工具 ----------
def echo(text: str) -> str:
    """Echoes back the input text."""
    return f"Echo: {text}"


def get_time() -> str:
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def delete_file(path: str) -> str:
    """Delete a file (演示用，实际不真删)."""
    return f"[SIMULATED] Deleted: {path}"


# ---------- 初始化 ----------
def init_session():
    if "engine" in st.session_state:
        return

    registry = ToolRegistry()
    registry.register(Tool(
        name="echo",
        description="Echoes back the input text.",
        func=echo,
        permission=PermissionLevel.FULL_TRUST,
    ))
    registry.register(Tool(
        name="get_time",
        description="Return current UTC time in ISO format.",
        func=get_time,
        permission=PermissionLevel.ASK_FIRST,
    ))
    registry.register(Tool(
        name="delete_file",
        description="Delete a file by path. Requires user approval.",
        func=delete_file,
        permission=PermissionLevel.ASK_FIRST,     # ← 需要审批
    ))

    st.session_state.engine = RuntimeEngine(
        model_provider=OpenAIProvider(),
        tool_registry=registry,
        memory=SimpleMemory(),
        permission_gate=PermissionGate(registry),
        max_turns=5,
    )
    st.session_state.history = []
    st.session_state.pending_approvals = []      # 待审批队列
    st.session_state.loop = asyncio.new_event_loop()


# ---------- 事件循环桥接 ----------
async def _collect(engine, user_input):
    return [ev async for ev in engine.run(user_input)]


def run_engine(engine, user_input=None):
    return st.session_state.loop.run_until_complete(_collect(engine, user_input))


# ---------- 处理事件流 ----------
def process_events(events, append_to=None):
    tool_logs = []
    new_pending = []
    final_text = None

    print("=== RAW EVENTS ===")
    for ev in events:
        if ev.startswith("[APPROVAL_REQUIRED]"):
            payload = json.loads(ev[len("[APPROVAL_REQUIRED] "):])
            new_pending.append(payload)
        elif ev.startswith("[Tool "):
            tool_logs.append(ev)
        else:
            final_text = ev
        print(repr(ev))
    print("==================")

    st.session_state.pending_approvals.extend(new_pending)

    if append_to is not None:
        append_to["tool_logs"] = tool_logs
        append_to["content"] = final_text or append_to.get("content", "")


# ---------- 页面 ----------
init_session()
engine = st.session_state.engine

st.title("🤖 Mini Harness")
st.caption("带权限审批的最小 Harness")

with st.sidebar:
    st.header("控制面板")
    st.write(f"模型：`{engine.model.model}`")
    st.write(f"消息数：**{len(engine.memory.messages)}**")
    if st.button("🗑️ 清空对话", use_container_width=True):
        engine.memory.messages.clear()
        engine.permission_gate.approved_call_ids.clear()
        st.session_state.history = []
        st.session_state.pending_approvals = []
        st.rerun()

# ---------- 渲染历史 ----------
for item in st.session_state.history:
    with st.chat_message(item["role"]):
        st.markdown(item["content"])
        for log in item.get("tool_logs", []):
            st.code(log, language="text")

# ---------- 待审批面板 ----------
if st.session_state.pending_approvals:
    st.warning(f"⚠️ 有 {len(st.session_state.pending_approvals)} 个工具调用等待审批")
    for idx, ap in enumerate(list(st.session_state.pending_approvals)):
        with st.container(border=True):
            st.markdown(f"**工具**：`{ap['tool_name']}`")
            st.markdown(f"**参数**：`{json.dumps(ap['arguments'], ensure_ascii=False)}`")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("✅ 批准", key=f"approve_{ap['call_id']}", use_container_width=True):
                    engine.permission_gate.approve(ap["call_id"])
                    st.session_state.pending_approvals = [
                        x for x in st.session_state.pending_approvals
                        if x["call_id"] != ap["call_id"]
                    ]
                    # 续跑引擎（不传 user_input）
                    with st.spinner("继续执行..."):
                        events = run_engine(engine, None)
                    # 把新事件追加到最近一条 assistant 记录
                    if st.session_state.history and st.session_state.history[-1]["role"] == "assistant":
                        process_events(events, st.session_state.history[-1])
                    else:
                        process_events(events)
                    st.rerun()
            with col2:
                if st.button("❌ 拒绝", key=f"deny_{ap['call_id']}", use_container_width=True):
                    # 直接把"拒绝"写入 memory 作为 tool 结果
                    engine.memory.add_tool_result(
                        ap["call_id"], f"[DENIED] 用户拒绝执行 '{ap['tool_name']}'"
                    )
                    st.session_state.pending_approvals = [
                        x for x in st.session_state.pending_approvals
                        if x["call_id"] != ap["call_id"]
                    ]
                    with st.spinner("继续执行..."):
                        events = run_engine(engine, None)
                    if st.session_state.history and st.session_state.history[-1]["role"] == "assistant":
                        process_events(events, st.session_state.history[-1])
                    else:
                        process_events(events)
                    st.rerun()

# ---------- 输入框 ----------
user_input = st.chat_input("输入你的问题...", disabled=bool(st.session_state.pending_approvals))
if user_input:
    st.session_state.history.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("思考中..."):
            events = run_engine(engine, user_input)

        assistant_entry = {"role": "assistant", "content": "", "tool_logs": []}
        process_events(events, assistant_entry)

        if assistant_entry["tool_logs"]:
            with st.expander(f"🔧 工具调用（{len(assistant_entry['tool_logs'])} 次）", expanded=True):
                for log in assistant_entry["tool_logs"]:
                    st.code(log, language="text")

        if assistant_entry["content"]:
            st.markdown(assistant_entry["content"])

    st.session_state.history.append(assistant_entry)
    if st.session_state.pending_approvals:
        st.rerun()