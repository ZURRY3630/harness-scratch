"""FastAPI 入口：uvicorn mini_harness.main:app --app-dir backend --port 8765

前端托管策略：
- frontend/dist/ 存在（npm run build 产物）→ 托管构建产物（推荐，生产模式）
- 不存在 → 回退到 frontend/index.html（兼容，但会提示先构建）
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.routes import flush_observability, get_project_config, router
from .core.config import get_config
from .observability import get_logger, setup_logging
from .observability.logging import log_format

log = get_logger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动：初始化内核日志；退出：把观测缓冲刷盘，避免丢尾部 trace。"""
    setup_logging()
    log.info("内核启动", version=__version__, project=get_project_config().name,
             log_format=log_format())
    try:
        yield
    finally:
        flush_observability()
        log.info("内核退出，观测缓冲已刷盘")


app = FastAPI(title="MiniHarness API", version=__version__, lifespan=lifespan)

cfg = get_config()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.cors_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

_FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend"
_DIST = _FRONTEND / "dist"


@app.get("/")
async def index():
    from fastapi.responses import FileResponse, HTMLResponse

    dist_index = _DIST / "index.html"
    if dist_index.exists():
        return FileResponse(dist_index)
    src_index = _FRONTEND / "index.html"
    if src_index.exists():
        return HTMLResponse(
            "<h3>前端未构建</h3><p>请在 frontend/ 目录执行 <code>npm install && npm run build</code> 后刷新。</p>"
        )
    return HTMLResponse("<h3>frontend/ 不存在</h3>", status_code=404)


# Vite 构建产物：/assets/*（js/css 带 hash 的静态资源）
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")


@app.get("/api/health")
async def health():
    """健康检查 + 当前项目信息（前端启动时取 agent_name 用于展示）。"""
    project = get_project_config()
    return {
        "status": "ok",
        "version": __version__,
        "frontend": "built" if _DIST.exists() else "not-built",
        "project": project.name,
        "agent_name": project.agent_name,
    }
