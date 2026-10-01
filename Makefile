SERVER_DIR := apps/server
WEB_DIR := apps/web

.PHONY: install-web run test lint eval ingest retrieval-eval variants demo clean lint-arch design-lint pg-up pg-down invite admin

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

# P21 用户体系：邀请码发放（内测封闭注册）/ 管理员提权（先注册再提权）
invite: pg-up
	cd $(SERVER_DIR) && uv run python scripts/auth_tool.py invite \
		$(if $(USES),--uses $(USES)) $(if $(DAYS),--days $(DAYS)) $(if $(NOTE),--note $(NOTE))

admin: pg-up
	cd $(SERVER_DIR) && uv run python scripts/auth_tool.py admin --email $(EMAIL)

# 全绿门禁口径：前置 pg-up，PG 依赖用例真跑；
# 裸跑 pytest 时无 PG 的用例会 Skip（醒目日志）。
test: pg-up
	cd $(SERVER_DIR) && uv run pytest -q

lint:
	cd $(SERVER_DIR) && uv run ruff check . && uv run ruff format --check .

eval:
	python3 eval/run_eval.py

# 语料入库（P15）：REBUILD=1 清库重建（换切片策略/embedding 模型后必须）；
# NO_EMBED=1 仅建 FTS 索引（检索侧会明确拒绝向量缺失）
ingest: pg-up
	cd $(SERVER_DIR) && uv run python ingest_main.py $(if $(REBUILD),--rebuild) $(if $(NO_EMBED),--no-embed)

# 检索层评测（P15）：dataset 的 factual/multi_hop 题 → Recall@k/MRR/NDCG@k。
# TAG=before|after 标注报告；NO_RERANK=1 / NO_REWRITE=1 / ROUNDS=2 分离方差；
# EXTRA=1 并入口语化变体集 eval/retrieval-queries.jsonl（make variants 重新生成）
retrieval-eval: pg-up
	cd $(SERVER_DIR) && uv run python ../../eval/run_retrieval_eval.py \
		$(if $(TAG),--tag $(TAG)) $(if $(NO_RERANK),--no-rerank) \
		$(if $(NO_REWRITE),--no-rewrite) $(if $(ROUNDS),--rounds $(ROUNDS)) \
		$(if $(EXTRA),--extra ../../eval/retrieval-queries.jsonl)

# 生成检索评测的口语化/同义改写变体（flash 小模型，gold 继承原题）
variants:
	cd $(SERVER_DIR) && uv run python ../../eval/gen_query_variants.py

# 一键起演示：双端（先 make install && make install-web）
demo:
	$(MAKE) -j2 run dev-web

dev-web:
# Node>=22.4 带 stub 版 localStorage 全局，React 19 dev 构建在 SSR 期探测会炸
# （localStorage.getItem is not a function）；给有效路径使其完整可用
	cd $(WEB_DIR) && NODE_OPTIONS="--localstorage-file=/tmp/gewu-web-ls" npm run dev

clean:
	rm -rf $(SERVER_DIR)/.pytest_cache $(SERVER_DIR)/.ruff_cache

# 架构依赖规则守护（P14-8 Python 版，零依赖：grep 断言）
lint-arch:
	bash scripts/lint-arch.sh

# 前端设计门禁（P20）：impeccable detect 三页 + 衬线域/h-screen/冷色 grep 规则，
# 剩余白名单项记录在仓库根 DESIGN.md「门禁白名单」区
design-lint:
	bash scripts/design-lint.sh

# 线上日志体检：拉服务器 journalctl 四层埋点 → 优化向报告（HOURS=48 可调窗口）
SERVER ?= root@117.72.163.14
DEPLOY_KEY ?= $(HOME)/Downloads/JD.pem
log-report:
	ssh -p 22 -i $(DEPLOY_KEY) $(SERVER) 'bash /root/gewu/scripts/log-report.sh $${HOURS:-24}'
