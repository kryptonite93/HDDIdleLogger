import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .api import router
from .collector import Collector
from .config import Config, Settings
from .database import Database
from .source_status import SourceStatus
from .request_capture import RequestCapture


def create_app(config=None, start_collector=True):
    config = config or Config()

    @asynccontextmanager
    async def lifespan(application):
        db = Database(config.data_dir / "profiler.sqlite3")
        settings = db.load_settings(Settings.from_env())
        db.save_settings(settings)
        collector = Collector(db, config, settings)
        application.state.db, application.state.collector = db, collector
        source_capture = RequestCapture(db, config) if os.getenv('REQUEST_ATTRIBUTION_ENABLED', '').lower() == 'true' else SourceStatus()
        application.state.attribution = source_capture
        if start_collector:
            collector.start()
            if isinstance(source_capture, RequestCapture):
                source_capture.start()
        try:
            yield
        finally:
            if isinstance(source_capture, RequestCapture):
                source_capture.stop()
            collector.stop()

    application = FastAPI(title="HDD Idle Profiler", lifespan=lifespan, docs_url=None, redoc_url=None)

    @application.middleware("http")
    async def local_mutations(request: Request, call_next):
        # Same-origin JSON mutations prevent browser-based cross-site drive-selection/reset requests.
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            from urllib.parse import urlsplit
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin changes are not allowed"}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "Send application/json"}, status_code=415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    application.include_router(router)
    root = Path(__file__).parent
    application.mount("/static", StaticFiles(directory=root / "static"), name="static")
    templates = Jinja2Templates(directory=root / "templates")

    @application.get("/")
    @application.get("/analysis")
    @application.get("/settings")
    @application.get("/disks/{device_name}")
    def page(request: Request, device_name: str | None = None):
        route = "disk" if device_name else request.url.path.strip("/") or "dashboard"
        return templates.TemplateResponse(request=request, name="app.html", context={"page": route, "device": device_name or "", "demo": getattr(application.state, "demo", False)})

    return application


app = create_app()


def run():
    import uvicorn
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8080")),
                log_level=os.getenv("LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    run()
