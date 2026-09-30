"""gewu 服务端入口：只做装配与启动（对齐 Go cmd/server/main.go 只做装配的约定）。"""

from __future__ import annotations

import os

import uvicorn

from gewu.api.app import create_app
from gewu.config import Settings, load_dotenv

load_dotenv()
settings = Settings.load()
app = create_app(settings)


def main() -> None:
    host, _, port = os.environ.get("SERVER_ADDR", "127.0.0.1:8000").partition(":")
    uvicorn.run(app, host=host or "127.0.0.1", port=int(port or 8000))


if __name__ == "__main__":
    main()
