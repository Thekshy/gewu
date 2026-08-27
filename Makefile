PY ?= python3
API_DIR := apps/api
WEB_DIR := apps/web
VENV := $(API_DIR)/.venv
PIP := $(VENV)/bin/pip
PYBIN := $(VENV)/bin/python

.PHONY: install-api install-web ingest dev-api dev-web test lint eval demo

install-api:
	$(PY) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e "$(API_DIR)[dev]"

install-web:
	cd $(WEB_DIR) && npm install

# 语料入库：有 LLM_API_KEY 时建「BM25+向量」混合索引，否则仅 BM25
ingest:
	$(PYBIN) -m app.rag.ingest

dev-api:
	cd $(API_DIR) && .venv/bin/uvicorn app.main:app --reload --port 8000

dev-web:
	cd $(WEB_DIR) && npm run dev

test:
	cd $(API_DIR) && .venv/bin/pytest -q

lint:
	cd $(API_DIR) && .venv/bin/ruff check .

eval:
	$(PYBIN) eval/run_eval.py

# 一键起演示：入库 + 双端（先 make install-api install-web）
demo: ingest
	$(MAKE) -j2 dev-api dev-web
