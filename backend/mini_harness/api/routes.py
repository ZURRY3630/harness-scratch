"""FastAPI 路由：会话管理 + SSE 聊天流 + 审批/转人工 + 长期记忆管理。"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..context.assembler import ContextAssembler
from ..context.budget import Budget
from ..context.prompt_loader import load_system_prompt
from ..memory.compressor import ContextCompressor
from ..memory.longterm import LongTermMemory
from ..memory.session_store import SessionStore
from ..models.openai_provider import OpenAIProvider
from ..runtime.engine import RuntimeEngine
from ..tools.levels import PermissionLevel
from ..tools.loader import ToolLoader
from ..tools.permission import PermissionGate
from ..tools.registry import ToolRegistry
from ..core.config import get_config

router = APIRouter(prefix="/api")

# 引擎容器：session_id -> RuntimeEngine（含其全部依赖）。单进程内存态；消息/审批/工具权限已持久化。
# 注：权限覆盖在 PermissionGate.effective_level() 每次决策时实时读取 DB，无需重建引擎。
_engines: dict[str, RuntimeEngine] = {}
_lock = asyncio.Lock()
_db = None          # 延迟初始化
_longterm = None

# 仓库根目录（backend/mini_harness/api/routes.py -> parents[3]）
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PROJECT_DIR = _REPO_ROOT / "projects" / "default"
_DEFAULT_PROMPT_PATH = _DEFAULT_PROJECT_DIR / "prompts" / "system.md"

# 默认内置工具白名单（顺序即工具列表展示顺序）
_DEFAULT_BUILTIN_TOOLS = ["echo", "get_time", "simulate_delete_file", "memory_save", "memory_search"]


def _prompt_cfg() -> SimpleNamespace:
    """System Prompt 来源（临时占位）。

    TODO(P0-1): Step 3 换成 `core.config.ProjectConfig`（从 configs/<project>.yaml 加载），
    此处不再出现任何领域字面量。
    """
    return SimpleNamespace(
        system_prompt_path=str(_DEFAULT_PROMPT_PATH),
        agent_name="MiniHarness",
        language="中文",
    )


def _tool_cfg() -> SimpleNamespace:
    """工具装载来源（临时占位）。

    TODO(P0-1): Step 3 换成 `core.config.ProjectConfig`。
    """
    return SimpleNamespace(
        tools={
            "builtin": list(_DEFAULT_BUILTIN_TOOLS),
            "plugins_dir": str(_DEFAULT_PROJECT_DIR / "tools"),
        }
    )


def _get_db():
    global _db, _longterm
    if _db is None:
        from ..persistence.database import Database
        cfg = get_config()
        _db = Database(cfg.memory_db_path)
        _longterm = LongTermMemory(_db, top_k=cfg.longterm_top_k)
    return _db


def get_longterm() -> LongTermMemory:
    _get_db()
    return _longterm


def build_engine(session_id: str) -> RuntimeEngine:
    """组装一个会话的引擎（依赖注入收口在这一处）。"""
    cfg = get_config()
    db = _get_db()
    store = SessionStore(db, session_id)
    registry = ToolRegistry(allowed_paths=["E:/code/ai/harness-scratch", "C:/Users/11383/.openclaw-autoclaw/workspace"])
    ToolLoader(registry, get_longterm(), session_id=session_id).load_all(_tool_cfg())
    gate = PermissionGate(registry, approval_store=db)
    budget = Budget(
        context_token_budget=cfg.context_token_budget,
        reserve_output_tokens=cfg.reserve_output_tokens,
        compress_threshold=cfg.compress_threshold,
    )
    compressor = ContextCompressor(db, OpenAIProvider(cache_stats=budget), keep_recent=cfg.keep_recent_messages)
    assembler = ContextAssembler(get_longterm())
    provider = OpenAIProvider(cache_stats=budget)
    return RuntimeEngine(
        provider=provider, registry=registry, store=store, gate=gate, budget=budget,
        compressor=compressor, assembler=assembler, longterm=get_longterm(),
        system_prompt=load_system_prompt(_prompt_cfg()),
        max_turns=cfg.max_turns, tool_timeout=cfg.tool_timeout_seconds,
    )


async def get_engine(session_id: str) -> RuntimeEngine:
    async with _lock:
        if session_id not in _engines:
            _engines[session_id] = build_engine(session_id)
        return _engines[session_id]


def sse_stream(engine: RuntimeEngine, agen) -> StreamingResponse:
    """把引擎事件流包成 SSE。"""
    async def gen():
        try:
            async for event in agen:
                yield f"data: {json.dumps(event.to_dict(), ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001 —— 任何异常都转为 error 事件下发
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': str(e)}, 'session_id': engine.store.session_id}, ensure_ascii=False)}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- 请求模型 ----------
class ChatRequest(BaseModel):
    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    message: str


class ApprovalRequest(BaseModel):
    session_id: str
    call_id: str
    decision: str  # approved | denied
    remember: bool = False  # APPROVE_ALWAYS：记住同类


class HandoffRequest(BaseModel):
    session_id: str
    call_id: str
    result: str   # 人工执行的结果


class MemoryCreate(BaseModel):
    content: str
    kind: str = "fact"


# ---------- 会话 ----------
@router.post("/sessions")
async def create_session(title: str = "New Chat"):
    sid = uuid.uuid4().hex[:12]
    _get_db().upsert_session(sid, title)
    return {"session_id": sid, "title": title}


@router.get("/sessions")
async def list_sessions():
    return _get_db().list_sessions()


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    _get_db().delete_session(session_id)
    async with _lock:
        _engines.pop(session_id, None)
    return {"deleted": session_id}


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    rows = _get_db().list_messages(session_id)
    return [
        {
            "role": r["role"],
            "content": r["content"],
            "tool_calls": json.loads(r["tool_calls"]) if r["tool_calls"] else None,
            "tool_call_id": r["tool_call_id"],
            "is_summary": bool(r["is_summary"]),
        }
        for r in rows
    ]


# ---------- 聊天（SSE）----------
@router.post("/chat")
async def chat(req: ChatRequest):
    engine = await get_engine(req.session_id)
    return sse_stream(engine, engine.run(req.message))


@router.post("/approvals")
async def approval(req: ApprovalRequest):
    engine = await get_engine(req.session_id)
    if req.decision == "approved":
        engine.gate.approve(req.call_id, remember=req.remember)
    else:
        engine.gate.deny(req.call_id)
    return sse_stream(engine, engine.resume())


@router.post("/handoff")
async def handoff(req: HandoffRequest):
    engine = await get_engine(req.session_id)
    engine.inject_tool_result_and_prepare(req.call_id, req.result)
    return sse_stream(engine, engine.resume())


# ---------- 工具管理 ----------
@router.get("/tools")
async def list_tools(session_id: Optional[str] = None):
    """列出全部工具及权限。

    - default: 代码声明的默认级别
    - effective: 实际生效级别（DB 覆盖 > 默认）
    - override: 存在的运行时覆盖（null 表示无）
    """
    db = _get_db()
    overrides = db.list_tool_permissions()
    engine = await get_engine(session_id) if session_id else None
    registry: ToolRegistry = engine.tools if engine else _fallback_registry()

    tools = []
    for tool in registry._tools.values():
        override = overrides.get(tool.name)
        eff = PermissionLevel(override) if override else tool.permission
        tools.append({
            "name": tool.name,
            "description": tool.description,
            "default": tool.permission.value,
            "effective": eff.value,
            "override": override,
            "path_guard": tool.path_arg is not None,
        })
    return tools


def _fallback_registry() -> ToolRegistry:
    """无 session 时用临时注册表仅做展示（权限元数据来自工具定义）。"""
    reg = ToolRegistry()
    ToolLoader(reg, get_longterm()).load_all(_tool_cfg())
    return reg


class ToolPermRequest(BaseModel):
    permission: str  # full_trust | auto_with_notification | ask_first | approve_always | manual_only


@router.put("/tools/{tool_name}/permission")
async def set_tool_permission(tool_name: str, req: ToolPermRequest, session_id: Optional[str] = None):
    """修改工具权限（运行时覆盖，持久化，立即生效）。"""
    try:
        level = PermissionLevel(req.permission)
    except ValueError:
        raise HTTPException(400, f"非法权限级别: {req.permission}（可用: {[l.value for l in PermissionLevel]}）")

    db = _get_db()
    engine = await get_engine(session_id) if session_id else None
    registry = engine.tools if engine else _fallback_registry()
    if registry.get(tool_name) is None:
        raise HTTPException(404, f"工具不存在: {tool_name}")

    db.set_tool_permission(tool_name, level.value)
    return {"tool": tool_name, "permission": level.value, "persisted": True}


@router.delete("/tools/{tool_name}/permission")
async def clear_tool_permission(tool_name: str, session_id: Optional[str] = None):
    """清除运行时覆盖，回到工具声明默认。"""
    db = _get_db()
    if db.get_tool_permission(tool_name) is None:
        raise HTTPException(404, f"工具 '{tool_name}' 无运行时覆盖")
    db.execute("DELETE FROM tool_permissions WHERE tool_name=?", (tool_name,))
    return {"tool": tool_name, "cleared": True}


# ---------- 长期记忆 ----------
@router.get("/memories")
async def list_memories(limit: int = 100):
    return get_longterm().list(limit)


@router.post("/memories")
async def create_memory(req: MemoryCreate):
    r = get_longterm().save(req.content, kind=req.kind)
    if not r.get("saved"):
        raise HTTPException(400, r.get("reason", "save failed"))
    return r


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str):
    get_longterm().delete(memory_id)
    return {"deleted": memory_id}


# ---------- 静态前端 ----------
async def serve_index():
    from fastapi.responses import FileResponse
    from pathlib import Path
    index = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(404, "frontend/index.html 不存在")
