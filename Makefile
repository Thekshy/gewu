BIN := bin/gewu-api
WEB_DIR := apps/web

.PHONY: build install-web ingest run test lint fmt vet eval demo clean lint-arch

# ---------- 单体（P8 起唯一形态） ----------

# 构建 Go 单体二进制（输出 bin/gewu-api）
build:
	go build -o $(BIN) ./cmd/server

# 语料入库：真调 EMBED_*（火山方舟）出向量，建「BM25+向量」混合索引。
# 可选参数：REBUILD=1 重建 / NO_EMBED=1 仅 BM25（显式手动选项）
ingest:
	go run ./cmd/server -ingest $(if $(REBUILD),-rebuild,) $(if $(NO_EMBED),-no-embed,)

# 启动单体 API（:8000）；先 make ingest 建索引
run:
	go run ./cmd/server

# ---------- 公共 ----------

install-web:
	cd $(WEB_DIR) && npm install

test:
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
