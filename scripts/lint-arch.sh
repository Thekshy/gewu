#!/usr/bin/env bash
# lint-arch（P14-8 Python 版）：模块依赖规则守护，零依赖（grep 断言）。
#
# 规则（docs/architecture.md §依赖规则，P8-4 Go 版的 Python 等价）：
#   api（接口层）→ agent/rag/business（只读展示）/支撑域，禁止直接 import llm；
#   rag / llm / business / memory / budget / middleware（支撑域）↛ agent（反向禁止）；
#   rag / llm / business 互相之间禁止横向依赖（经编排层解耦）；
#   main.py 只做装配（只 import gewu.api/config 一线）。
# 违规即非零退出；`make lint-arch`。
set -u
cd "$(dirname "$0")/.."
fail=0

check() { # check <描述> <文件glob> <禁止pattern>
    local desc="$1" glob="$2" pat="$3"
    if grep -rn "$pat" $glob 2>/dev/null | grep -v "_test.py" | grep -q .; then
        echo "lint-arch 违规：$desc"
        grep -rn "$pat" $glob 2>/dev/null | grep -v "_test.py" | head -3
        fail=1
    fi
}

# app.py 是装配工厂（Go cmd/server/main.go 的等价物，构造 LLMService 合法）；
# 路由实现层（routes/chat）禁止直接 import llm。
check "api 路由层禁止直接 import llm" "apps/server/gewu/api/routes.py apps/server/gewu/api/chat.py" "from gewu.llm\|import gewu.llm"
check "rag ↛ agent（反向）" "apps/server/gewu/rag" "from gewu.agent\|import gewu.agent"
check "llm ↛ agent（反向）" "apps/server/gewu/llm" "from gewu.agent\|import gewu.agent"
check "business ↛ agent（反向）" "apps/server/gewu/business" "from gewu.agent\|import gewu.agent"
check "business ↛ rag（经编排层解耦）" "apps/server/gewu/business" "from gewu.rag\|import gewu.rag"
check "llm ↛ rag（横向禁止）" "apps/server/gewu/llm" "from gewu.rag\|import gewu.rag"
check "memory/budget/middleware/auth ↛ 业务域" "apps/server/gewu/memory.py apps/server/gewu/budget.py apps/server/gewu/middleware.py apps/server/gewu/auth" "from gewu.\(agent\|rag\|business\)"

# main.py 只做装配：import 面只允许 gewu.api / gewu.config
if grep -E "^from gewu\.|^import gewu" apps/server/main.py | grep -v "gewu.api\|gewu.config" | grep -q .; then
    echo "lint-arch 违规：main.py 只做装配（仅允许 import gewu.api/gewu.config）"
    grep -E "^from gewu\.|^import gewu" apps/server/main.py | grep -v "gewu.api\|gewu.config"
    fail=1
fi

if [ "$fail" -ne 0 ]; then exit 1; fi
echo "lint-arch：依赖规则全部合规"
