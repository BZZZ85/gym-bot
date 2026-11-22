"""Run the Telegram bot and Mini App together in one Railway service."""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent
LOGGER = logging.getLogger("gym")


def public_url(environment):
    explicit = environment.get("WEBAPP_URL", "").strip()
    domain = environment.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    address = explicit or (f"https://{domain}" if domain else "")
    if address:
        parsed = urlparse(address)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("WEBAPP_URL должен быть публичным HTTPS-адресом приложения")
    return address


def environment_for_children():
    from dotenv import load_dotenv

    load_dotenv(ROOT / "ton.env")
    load_dotenv(ROOT / ".env")
    environment = dict(os.environ)
    if not environment.get("BOT_TOKEN") or not environment.get("DATABASE_URL"):
        raise ValueError("Задайте BOT_TOKEN и DATABASE_URL в Railway Variables")
    try:
        port = int(environment.get("PORT", "8000"))
    except ValueError as exc:
        raise ValueError("PORT должен быть числом от 1 до 65535") from exc
    if not 1 <= port <= 65535:
        raise ValueError("PORT должен быть числом от 1 до 65535")
    environment["WEBAPP_URL"] = public_url(environment)
    environment["PYTHONUNBUFFERED"] = "1"
    return environment, port


def wait_for_web(process, port, stopping, timeout=90):
    # Probe our own server directly, independent of deployment proxy settings.
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while not stopping.is_set():
        code = process.poll()
        if code is not None:
            raise RuntimeError(f"Веб-приложение завершилось при запуске (код {code})")
        if time.monotonic() >= deadline:
            raise RuntimeError("Веб-приложение не запустилось за 90 секунд. Проверьте DATABASE_URL и логи")
        try:
            with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                if response.status == 200 and json.load(response).get("status") == "ok":
                    return True
        except (HTTPError, URLError, OSError, ValueError):
            pass
        stopping.wait(0.25)
    return False


def watch_children(children, stopping):
    while not stopping.is_set():
        for name, process in children:
            code = process.poll()
            if code is not None:
                LOGGER.error("%s завершился (код %s). Завершаем общий сервис", name, code)
                return code if code > 0 else 1
        stopping.wait(0.25)
    return 0


def stop_children(children):
    # Signal both children before waiting, so shutdown stays bounded.
    for _, process in reversed(children):
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 15
    for name, process in reversed(children):
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            LOGGER.warning("%s не завершился вовремя; останавливаем процесс", name)
            try:
                process.kill()
            except ProcessLookupError:
                pass
            process.wait(timeout=5)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stopping = threading.Event()
    previous_signals = {}
    children = []

    def request_stop(signum, frame):
        stopping.set()

    try:
        environment, port = environment_for_children()
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_signals[signum] = signal.signal(signum, request_stop)
        LOGGER.info("Запускаем Mini App на порту %s", port)
        web = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "webapp.main:app", "--host", "0.0.0.0",
             "--port", str(port), "--workers", "1", "--timeout-graceful-shutdown", "10"],
            cwd=ROOT, env=environment,
        )
        children.append(("Mini App", web))
        if not wait_for_web(web, port, stopping):
            return 0
        if stopping.is_set():
            return 0
        LOGGER.info("Mini App готово. Запускаем Telegram-бота")
        bot = subprocess.Popen([sys.executable, "bot_mvp.py"], cwd=ROOT, env=environment)
        children.append(("Telegram-бот", bot))
        if not environment["WEBAPP_URL"]:
            LOGGER.info("Для кнопки приложения создайте домен Railway или задайте WEBAPP_URL")
        return watch_children(children, stopping)
    except (OSError, ValueError, RuntimeError) as exc:
        LOGGER.error("Не удалось запустить сервис: %s", exc)
        return 1
    finally:
        stop_children(children)
        for signum, previous in previous_signals.items():
            signal.signal(signum, previous)


if __name__ == "__main__":
    sys.exit(main())
