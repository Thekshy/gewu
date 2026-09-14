BIN := bin/gewu-api
WEB_DIR := apps/web

.PHONY: build install-web ingest run test lint fmt vet eval demo clean lint-arch pg-up pg-down

# ---------- 单体（P8 起唯一形态） ----------

# 构建 Go 单体二进制（输出 bin/gewu-api）
build:
	go build -o $(BIN) ./cmd/server

# ---------- 检索存储（P12：PostgreSQL + pgvector） ----------

# 起 PG（pgvector/pgvector:pg17，宿主 127.0.0.1:5433，--wait 等 healthcheck）。
# 本机 docker daemon 不可用时回退宿主 Homebrew PG（brew install postgresql@17 pgvector）。
pg-up:
	@docker compose up -d --wait pg 2>/dev/null || { \
		echo "[pg-up] docker 不可用，回退宿主 Homebrew PG（postgresql@17，:5433）"; \
		/opt/homebrew/opt/postgresql@17/bin/pg_ctl -D /opt/homebrew/var/postgresql@17 status >/dev/null 2>&1 || \
		/opt/homebrew/opt/postgresql@17/bin/pg_ctl -D /opt/homebrew/var/postgresql@17 start -o "-p 5433" -l /tmp/gewu-pg.log; \
		/opt/homebrew/opt/postgresql@17/bin/pg_isready -q -p 5433 || \
		(echo "[pg-up] 宿主 PG 未就绪：先 CREATE ROLE gewu LOGIN PASSWORD 'gewu' 与 DB gewu 并 CREATE EXTENSION vector"; exit 1); \
	}

pg-down:
	@docker compose stop pg 2>/dev/null || /opt/homebrew/opt/postgresql@17/bin/pg_ctl -D /opt/homebrew/var/postgresql@17 stop -m fast

# 语料入库：真调 EMBED_*（火山方舟）出向量，建「BM25+向量」混合索引。
# 可选参数：REBUILD=1 重建 / NO_EMBED=1 仅 BM25（显式手动选项）
ingest:
	go run ./cmd/server -ingest $(if $(REBUILD),-rebuild,) $(if $(NO_EMBED),-no-embed,)

# 启动单体 API（:8000）；先 make pg-up && make ingest 建索引
run:
	go run ./cmd/server

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
