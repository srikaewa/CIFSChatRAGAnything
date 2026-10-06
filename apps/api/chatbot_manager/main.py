import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from .admin import router as admin_router
from .db import get_engine, init_db
from .operations.scheduler import OperationsScheduler
from .settings import get_settings, validate_deployment_settings
from .webhooks import router as webhook_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    validate_deployment_settings(settings)
    init_db()

    scheduler_session = Session(get_engine())
    scheduler = OperationsScheduler(scheduler_session, settings)
    stop_event = asyncio.Event()
    task = asyncio.create_task(scheduler.run_forever(stop_event))
    app.state.operations_scheduler_task = task
    app.state.operations_scheduler_stop_event = stop_event
    try:
        yield
    finally:
        stop_event.set()
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
        scheduler_session.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Chatbot Manager", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(webhook_router)
    app.include_router(admin_router)
    return app


app = create_app()
