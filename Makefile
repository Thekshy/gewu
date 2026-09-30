BIN := bin/gewu-api
WEB_DIR := apps/web
SERVER_DIR := apps/server

.PHONY: build install-web ingest run test lint fmt vet eval demo clean lint-arch pg-up pg-down server-install server-run server-test server-lint

# ---------- 单体（P8 起唯一形态） ----------

# 构建 Go 单体二进制（输出 bin/gewu-api）
build:
	go build -o $(BIN) ./cmd/server

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

# 语料入库：真调 EMBED_*（火山方舟）出向量，建「FTS+向量」混合索引（PG+pgvector）。
# 可选参数：REBUILD=1 清库重建 / NO_EMBED=1 仅 FTS（显式手动选项）
ingest:
	go run ./cmd/server -ingest $(if $(REBUILD),-rebuild,) $(if $(NO_EMBED),-no-embed,)

# 启动单体 API（:8000）；先 make pg-up && make ingest 建索引
run:
	go run ./cmd/server

# ---------- Python 服务端（P14 LangGraph 迁移，双轨期 :8001） ----------

# 同步依赖（uv 管 pyproject + uv.lock）
server-install:
	cd $(SERVER_DIR) && uv sync

# 启动 Python 服务端（双轨期 :8001；P14-8 Go 退役后收口 :8000）
server-run:
	cd $(SERVER_DIR) && uv run uvicorn main:app --host 127.0.0.1 --port 8001

server-test:
	cd $(SERVER_DIR) && uv run pytest -q

server-lint:
	cd $(SERVER_DIR) && uv run ruff check . && uv run ruff format --check .

# ---------- 公共 ----------

install-web:
	cd $(WEB_DIR) && npm install

# 全绿门禁口径（P12 起）：前置 pg-up，PG 依赖用例真跑；
# 裸跑 `go test ./...` 时无 PG 的用例会 Skip（醒目日志）。
test: pg-up
	go test ./...

lint: fmt vet

fmt:
	gofmt -l -w cmd internal scripts

vet:
	go vet ./...

eval:
	python3 eval/run_eval.py

# 一键起演示：入库 + 双端（先 make install-web）
demo: ingest
	$(MAKE) -j2 run dev-web

dev-web:
	cd $(WEB_DIR) && npm run dev

clean:
	rm -rf bin

# 架构依赖规则守护（P8-4）：违规即非零退出
lint-arch:
	bash scripts/lint-arch.sh
