"""Start the GYM Mini App as one web process on Railway."""

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def server_port(environment):
    try:
        port = int(environment.get("PORT", "8080"))
    except (TypeError, ValueError) as exc:
        raise ValueError("PORT должен быть числом от 1 до 65535") from exc
    if not 1 <= port <= 65535:
        raise ValueError("PORT должен быть числом от 1 до 65535")
    return port


def main():
    from dotenv import load_dotenv
    import uvicorn

    load_dotenv(ROOT / "ton.env")
    load_dotenv(ROOT / ".env")
    try:
        port = server_port(os.environ)
    except ValueError as exc:
        logging.error("%s", exc)
        return 1
    uvicorn.run(
        "webapp.main:app", host="0.0.0.0", port=port, workers=1,
        app_dir=str(ROOT), timeout_graceful_shutdown=10,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
