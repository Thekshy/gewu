API_DIR := apps/api
WEB_DIR := apps/web
BIN := bin/gewu-api

.PHONY: build install-web ingest run test lint fmt vet eval demo clean

# 构建 Go 二进制（输出 bin/gewu-api）
build:
	go build -o $(BIN) ./cmd/server

install-web:
	cd $(WEB_DIR) && npm install

# 语料入库：有 LLM_API_KEY 时建「BM25+向量」混合索引，否则仅 BM25
ingest:
	go run ./cmd/server -ingest

# 启动 API（:8000）；先 make ingest 建索引
run:
	go run ./cmd/server

test:
	go test ./...

lint: fmt vet

fmt:
	gofmt -l -w cmd internal

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
