from fastapi import FastAPI

from .config import get_settings
from .contracts import TurnRequest, TurnResponse
from .orchestrator import handle_turn


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/turns", response_model=TurnResponse)
    async def create_turn(request: TurnRequest) -> TurnResponse:
        answer, reports, elapsed_ms = await handle_turn(request.message)
        return TurnResponse(answer=answer, reports=reports, elapsed_ms=elapsed_ms)

    return app


app = create_app()

