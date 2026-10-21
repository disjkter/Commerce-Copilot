import uvicorn

from .config import get_settings
from .observability import configure_logging


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        "commerce_copilot.api:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )


if __name__ == "__main__":
    run()

