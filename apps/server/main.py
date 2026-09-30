"""gewu 服务端入口：只做装配与启动（对齐 cmd/server/main.go 只做装配的约定）。

P14 双轨期缺省 :8001（Go 版仍占 :8000），P14-8 Go 退役后收口切 :8000。
"""

from __future__ import annotations

import os

import uvicorn

from gewu.api.app import create_app
from gewu.config import Settings, load_dotenv

load_dotenv()
settings = Settings.load()
app = create_app(settings)


def main() -> None:
    host, _, port = os.environ.get("SERVER_ADDR", "127.0.0.1:8001").partition(":")
    uvicorn.run(app, host=host or "127.0.0.1", port=int(port or 8001))


if __name__ == "__main__":
    main()
