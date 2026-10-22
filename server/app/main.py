import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, storage
from .api import download, transfers, ws

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# The built frontend can live anywhere; the dev layout puts it in ../web/dist.
WEB_DIR = Path(os.getenv("WEB_DIR", str(config.BASE_DIR.parent / "web" / "dist")))


async def _cleanup_loop() -> None:
    while True:
        try:
            removed = await asyncio.to_thread(storage.cleanup_once)
            if removed:
                log.info("cleaned up %s expired transfers", removed)
        except Exception:
            log.exception("cleanup failed")
        await asyncio.sleep(config.CLEANUP_INTERVAL_SEC)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    await asyncio.to_thread(storage.cleanup_once)
    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    db.close()


app = FastAPI(title="文件快传", lifespan=lifespan, docs_url=None, redoc_url=None)


app = FastAPI(title="文件快传", lifespan=lifespan, docs_url=None, redoc_url=None)

# The Android shell loads its UI from http://localhost while talking to the
# server's IP, so every API call is cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "HEAD", "OPTIONS"],
    allow_headers=["Content-Type", "Range"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Disposition"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})


app.include_router(transfers.router)
app.include_router(download.router)
app.include_router(ws.router)

if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
