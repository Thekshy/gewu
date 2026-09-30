SERVER_DIR := apps/server
WEB_DIR := apps/web

.PHONY: install-web run test lint eval demo clean lint-arch pg-up pg-down

# ---------- 检索存储（P12：PostgreSQL + pgvector） ----------

# 起 PG（优先 compose；本机 docker 不可用回退宿主 Homebrew PG）。
# 宿主实例可能是 brew services 管理的共享 postmaster（多项目共用，端口 5432），
# 也可能是 pg_ctl 手起的 5433——两端口任一就绪即通过，都不在才尝试拉起。
pg-up:
	@docker compose up -d --wait pg 2>/dev/null || { \
		echo "[pg-up] docker 不可用，回退宿主 Homebrew PG（postgresql@17）"; \
		/opt/homebrew/opt/postgresql@17/bin/pg_isready -q -p 5433 || \
		/opt/homebrew/opt/postgresql@17/bin/pg_isready -q -p 5432 || \
		/opt/homebrew/opt/postgresql@17/bin/pg_ctl -D /opt/homebrew/var/postgresql@17 start -o "-p 5433" -l /tmp/gewu-pg.log; \
		/opt/homebrew/opt/postgresql@17/bin/pg_isready -q -p 5433 || \
		/opt/homebrew/opt/postgresql@17/bin/pg_isready -q -p 5432 || \
		(echo "[pg-up] 宿主 PG 未就绪（5432/5433 均无）：brew services start postgresql@17，并按 README 建库建号"; exit 1); \
	}

# 宿主路径下不代停：该实例是多项目共享 postmaster（停了会殃及其他项目），
# 需要真停用 brew services stop postgresql@17。
pg-down:
	@docker compose stop pg 2>/dev/null || echo "[pg-down] 宿主 PG 为多项目共享实例，不代停；确需停用：brew services stop postgresql@17"

# ---------- Python 服务端（P14 起：LangGraph 编排，:8000） ----------

# 同步依赖（uv 管 pyproject + uv.lock）
install:
	cd $(SERVER_DIR) && uv sync

# 启动 API（:8000）；先 make pg-up，并确保索引已入库
run:
	cd $(SERVER_DIR) && uv run uvicorn main:app --host 127.0.0.1 --port 8000

# ---------- 公共 ----------

install-web:
	cd $(WEB_DIR) && npm install

# 全绿门禁口径：前置 pg-up，PG 依赖用例真跑；
# 裸跑 pytest 时无 PG 的用例会 Skip（醒目日志）。
test: pg-up
	cd $(SERVER_DIR) && uv run pytest -q

lint:
	cd $(SERVER_DIR) && uv run ruff check . && uv run ruff format --check .

eval:
	python3 eval/run_eval.py

# 一键起演示：双端（先 make install && make install-web）
demo:
	$(MAKE) -j2 run dev-web

dev-web:
	cd $(WEB_DIR) && npm run dev

clean:
	rm -rf $(SERVER_DIR)/.pytest_cache $(SERVER_DIR)/.ruff_cache

# 架构依赖规则守护（P14-8 Python 版，零依赖：grep 断言）
lint-arch:
	bash scripts/lint-arch.sh
