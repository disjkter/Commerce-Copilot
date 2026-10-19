import uvicorn

from .config import get_settings


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "commerce_copilot.api:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )


if __name__ == "__main__":
    run()

