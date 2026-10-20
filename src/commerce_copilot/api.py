from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .contracts import TurnRequest, TurnResponse
from .orchestrator import handle_turn


WEB_ROOT = Path(__file__).with_name("web")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.mount("/assets", StaticFiles(directory=WEB_ROOT), name="assets")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(WEB_ROOT / "index.html")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/turns", response_model=TurnResponse)
    async def create_turn(request: TurnRequest) -> TurnResponse:
        return await handle_turn(request.message, request.conversation_id)

    return app


app = create_app()

