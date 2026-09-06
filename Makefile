BIN := bin/gewu-api
WEB_DIR := apps/web
MS_SERVICES := gateway orchestrator conversation generate tool rag

.PHONY: build build-ms install-web ingest run run-ms test lint fmt vet eval demo clean buf-lint buf-generate compose-ms compose-monolith

# ---------- 单体（冻结基线：A/B 对照与回退） ----------

# 构建 Go 单体二进制（输出 bin/gewu-api）
build:
	go build -o $(BIN) ./cmd/server

# 语料入库：真调 EMBED_*（火山方舟）出向量，建「BM25+向量」混合索引。
# 可选参数：REBUILD=1 重建 / NO_EMBED=1 仅 BM25（显式手动选项）
ingest:
	go run ./cmd/server -ingest $(if $(REBUILD),-rebuild,) $(if $(NO_EMBED),-no-embed,)

# 微服务侧入库（P3 起）：经 rag 服务 Redis Streams 流水线，阻塞至完成回执。
# 服务需已启动（compose 或 run-ms）；可选 NO_EMBED=1 / REBUILD=1
ingest-ms:
	go run ./cmd/rag -ingest $(if $(NO_EMBED),-no-embed,) $(if $(REBUILD),-rebuild,)

# 启动单体 API（:8000）；先 make ingest 建索引
run:
	go run ./cmd/server

# ---------- 微服务（P0 起） ----------

# 构建六个微服务二进制到 bin/
build-ms:
	@for s in $(MS_SERVICES); do go build -o bin/$$s ./cmd/$$s || exit 1; done
	@echo "已构建：$(MS_SERVICES) → bin/"

# 本机直跑六服务（各自终端/后台；先起基础设施：docker compose up -d postgres redis）
run-ms: build-ms
	@for s in $(MS_SERVICES); do bin/$$s & done
	@echo "六服务已后台启动（gateway :8000）"

# proto 校验与代码生成（生成到 pkg/gen；改动 proto 后必须重跑并提交）
buf-lint:
	buf lint

buf-generate:
	buf generate

# compose 起全链路（六服务 + pg/redis；零 key 模式可跑）
compose-ms:
	docker compose up -d --wait

# 向量栈（P3 起需要）：--profile milvus
compose-milvus:
	docker compose --profile milvus up -d --wait

# 冻结单体 A/B 对照（宿主 :8001）
compose-monolith:
	docker compose --profile monolith up -d --wait api

# ---------- 公共 ----------

install-web:
	cd $(WEB_DIR) && npm install

test:
	go test ./...

lint: fmt vet

fmt:
	gofmt -l -w cmd internal pkg

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
