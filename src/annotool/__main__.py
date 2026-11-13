"""`python -m annotool` – start the server and open the browser."""
from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from .app import create_app


def main() -> None:
    p = argparse.ArgumentParser(prog="annotool")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true")
    a = p.parse_args()
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{a.host}:{a.port}")).start()
    try:
        uvicorn.run(create_app(), host=a.host, port=a.port)
    except OSError as exc:  # e.g. the port is already in use
        raise SystemExit(f"Could not start on {a.host}:{a.port} ({exc}). Is annotool already running? Try --port 8001.")


if __name__ == "__main__":
    main()
