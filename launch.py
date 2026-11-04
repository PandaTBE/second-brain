"""Launch the loopback settings app and open its browser window."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import webbrowser

import uvicorn

APP_ID = "second-brain-settings"


def is_running(url: str) -> bool:
    """Recognize this application without trusting an arbitrary occupied port."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{url}/api/health", timeout=0.5) as response:
            body = json.loads(response.read(4096))
        return (
            isinstance(body, dict)
            and isinstance(body.get("data"), dict)
            and body["data"].get("app_id") == APP_ID
        )
    except (OSError, ValueError, urllib.error.URLError):
        return False


def open_when_ready(url: str) -> None:
    """Open the page only after the actual server responds."""
    for _ in range(100):
        if is_running(url):
            webbrowser.open(url)
            return
        time.sleep(0.1)
    print(f"Страница доступна по адресу {url}", flush=True)


def main() -> None:
    """Reuse an existing instance or start the local server."""
    try:
        port = int(os.environ.get("SECOND_BRAIN_PORT", "8765"))
        if not 1024 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise SystemExit("SECOND_BRAIN_PORT должен быть числом от 1024 до 65535.") from None

    url = f"http://127.0.0.1:{port}"
    if is_running(url):
        webbrowser.open(url)
        return

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))
    except OSError:
        raise SystemExit(
            f"Порт {port} занят другим приложением. "
            "Укажите другой: SECOND_BRAIN_PORT=8766 ./start.command"
        ) from None

    print(f"Second Brain: {url}\nЧтобы остановить приложение, нажмите Control+C.", flush=True)
    threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
    uvicorn.run(
        "brain_settings.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=port,
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
