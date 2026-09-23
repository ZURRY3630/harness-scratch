"""FastAPI 入口：uvicorn mini_harness.main:app --app-dir backend --port 8765

前端托管策略：
- frontend/dist/ 存在（npm run build 产物）→ 托管构建产物（推荐，生产模式）
- 不存在 → 回退到 frontend/index.html（兼容，但会提示先构建）
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .core.config import get_config

app = FastAPI(title="MiniHarness API", version="0.3.0")

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
    return {"status": "ok", "version": "0.3.0", "frontend": "built" if _DIST.exists() else "not-built"}
