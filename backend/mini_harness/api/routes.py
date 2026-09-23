"""FastAPI 路由：会话管理 + SSE 聊天流 + 审批/转人工 + 长期记忆管理。

组装层（build_engine）是**纯配置驱动**的：这里只做反射构造与依赖注入，
任何"哪个 Provider / 哪些工具 / 什么提示词"的决定都来自 ProjectConfig。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..context.assembler import ContextAssembler
from ..context.budget import Budget
from ..context.prompt_loader import load_system_prompt
from ..core.config import ProjectConfig, load_project_config
from ..core.hooks import load_hooks
from ..memory.compressor import ContextCompressor
from ..memory.longterm import LongTermMemory
from ..memory.session_store import SessionStore
from ..models.openai_provider import OpenAIProvider
from ..runtime.engine import RuntimeEngine
from ..tools.levels import PermissionLevel
from ..tools.loader import ToolLoader
from ..tools.permission import PermissionGate
from ..tools.registry import ToolRegistry

router = APIRouter(prefix="/api")

# 引擎容器：session_id -> RuntimeEngine（含其全部依赖）。单进程内存态；消息/审批/工具权限已持久化。
# 注：权限覆盖在 PermissionGate.effective_level() 每次决策时实时读取 DB，无需重建引擎。
_engines: dict[str, RuntimeEngine] = {}
_lock = asyncio.Lock()
_db = None          # 延迟初始化
_longterm = None
_project_config: ProjectConfig | None = None

# 未指定 PROJECT_CONFIG_PATH 时的默认项目配置
_DEFAULT_PROJECT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "projects" / "default" / "config.yaml"


def get_project_config() -> ProjectConfig:
    """当前项目配置（进程级单例）：PROJECT_CONFIG_PATH > projects/default/config.yaml。

    一个后端进程服务一个项目；换项目 = 换 YAML + 重启，内核零改动。
    """
    global _project_config
    if _project_config is None:
        path = os.getenv("PROJECT_CONFIG_PATH") or str(_DEFAULT_PROJECT_CONFIG_PATH)
        _project_config = load_project_config(path)
    return _project_config


def _get_db(cfg: ProjectConfig | None = None):
    """DB 是进程级单例，取首次传入的配置（即当前项目配置）。"""
    global _db, _longterm
    if _db is None:
        from ..persistence.database import Database
        cfg = cfg or get_project_config()
        _db = _build_database(cfg)
        _longterm = LongTermMemory(_db, top_k=int(cfg.memory["longterm_top_k"]))
    return _db


def get_longterm() -> LongTermMemory:
    _get_db()
    return _longterm


# ----------------------------------------------------------------------
# 组装层：配置 -> 组件。此处只允许出现"构造 + 注入"，不允许出现任何领域判断。
# ----------------------------------------------------------------------
def _build_database(cfg: ProjectConfig):
    """TODO(P1-1): 改为 ComponentRegistry.build("memory", cfg.memory["type"], ...)。"""
    memory_type = cfg.memory["type"]
    if memory_type != "sqlite":
        raise ValueError(f"未知记忆后端类型: {memory_type}（当前仅支持 sqlite）")
    from ..persistence.database import Database
    return Database(cfg.memory["path"])


def _build_provider(cfg: ProjectConfig, budget: Budget) -> OpenAIProvider:
    """TODO(P1-1): 改为 ComponentRegistry.build("provider", cfg.provider["type"], ...)。"""
    provider_type = cfg.provider["type"]
    if provider_type != "openai":
        raise ValueError(f"未知模型供应商类型: {provider_type}（当前仅支持 openai）")
    p = cfg.provider
    return OpenAIProvider(
        model=p["model"],
        api_key=p["api_key"],
        base_url=p["base_url"],
        temperature=float(p["temperature"]),
        max_retries=int(p["max_retries"]),
        request_timeout=float(p["request_timeout"]),
        cache_stats=budget,
    )


def _allowed_paths(cfg: ProjectConfig) -> list[str]:
    """工具路径边界；环境变量 HARNESS_ALLOWED_PATHS（逗号/分号分隔）优先。"""
    raw = (os.getenv("HARNESS_ALLOWED_PATHS") or "").strip()
    if raw:
        return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    return [p for p in cfg.permission["allowed_paths"] if p]


def _build_registry(cfg: ProjectConfig, session_id: str) -> ToolRegistry:
    registry = ToolRegistry(allowed_paths=_allowed_paths(cfg))
    ToolLoader(registry, get_longterm(), session_id=session_id).load_all(cfg)
    return registry


def build_engine(session_id: str, cfg: ProjectConfig) -> RuntimeEngine:
    """组装一个会话的引擎：构造 + 注入，其余全由 cfg 决定。"""
    db = _get_db(cfg)
    budget = Budget(
        context_token_budget=int(cfg.budget["context_token_budget"]),
        reserve_output_tokens=int(cfg.budget["reserve_output_tokens"]),
        compress_threshold=float(cfg.budget["compress_threshold"]),
    )
    provider = _build_provider(cfg, budget)
    store = SessionStore(db, session_id)
    registry = _build_registry(cfg, session_id)
    gate = PermissionGate(
        registry,
        approval_store=db if cfg.permission.get("approval_store", True) else None,
    )
    compressor = ContextCompressor(db, provider, keep_recent=int(cfg.compressor["keep_recent"]))
    assembler = ContextAssembler(get_longterm())
    return RuntimeEngine(
        provider=provider, registry=registry, store=store, gate=gate, budget=budget,
        compressor=compressor, assembler=assembler, longterm=get_longterm(),
        system_prompt=load_system_prompt(cfg),
        max_turns=cfg.max_turns, tool_timeout=cfg.tool_timeout,
        hook_chain=load_hooks(cfg.hooks),
    )


async def get_engine(session_id: str) -> RuntimeEngine:
    async with _lock:
        if session_id not in _engines:
            _engines[session_id] = build_engine(session_id, get_project_config())
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
    return _build_registry(get_project_config(), session_id="")


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
