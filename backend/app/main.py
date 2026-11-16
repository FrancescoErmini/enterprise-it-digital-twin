"""digital-twin-api: FastAPI entrypoint."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.v1.routes import router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.platform import PlatformServices

logger = logging.getLogger("digital-twin-api")


def _wait_for_graph(platform: PlatformServices, timeout_s: float = 120) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if platform.client.ping():
            return
        time.sleep(2)
    raise RuntimeError("Technology graph not reachable")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    platform = PlatformServices(get_settings())
    platform.install_usecases()
    _wait_for_graph(platform)
    app.state.startup_report = platform.startup()
    logger.info("Startup report: %s", app.state.startup_report)
    app.state.platform = platform
    yield
    platform.client.close()


app = FastAPI(title="Enterprise IT Digital Twin API", version="0.1.0", lifespan=lifespan,
              description="digital-twin-api: technology graph, IT Intelligence Agent and registered use cases. "
                          "All POC data is synthetic.")
app.include_router(router)


@app.get("/health")
def health() -> JSONResponse:
    platform: PlatformServices | None = getattr(app.state, "platform", None)
    graph_ok = bool(platform and platform.client.ping())
    body = {"status": "ok" if graph_ok else "degraded", "graph": "reachable" if graph_ok else "unreachable",
            "usecases": platform.usecases.keys() if platform else [],
            "llm_configured": bool(platform and platform.settings.openai_api_key)}
    return JSONResponse(body, status_code=200 if graph_ok else 503)
