#!/usr/bin/env bash
# lint-arch：单体模块化依赖规则守护（P8-4，零依赖：go list + grep）。
#
# 模块地图与依赖规则（docs/architecture.md §依赖规则）：
#   api（接口层）→ agent/rag/business（只读展示）/支撑域，禁止直接 import llm；
#   agent（编排域）→ rag / llm / business（经接口）；
#   rag / llm / business ↛ agent（反向禁止）；
#   business 只经 agent.Tools（权限矩阵单一出口）被触达，不 import rag；
#   支撑域（budget/config/dates/middleware）可被任何域用，但不 import 业务域；
#   cmd/server 只做装配（白名单内的 internal 包）。
# 违规即非零退出；`make lint-arch`。
set -u

# 健康检查：go list 本身失败（编译错误/cycle）必须报错，不能静默当作合规。
if ! go list ./internal/... ./cmd/... > /dev/null 2>&1; then
    echo "lint-arch：go list 失败（存在编译错误或 import cycle），先修复再跑守护" >&2
    exit 1
fi

violations=""

# deps <./pkg/...>：列出包路径与其全部 imports（go list 标准格式）。
deps() { go list -f '{{.ImportPath}} {{.Imports}}' "$1" 2>&1; }

# forbid <glob> <regex> <规则说明>：命中即记录违规。
forbid() {
    local glob="$1" regex="$2" rule="$3" hit
    hit=$(deps "$glob" | grep -E "gewu/$regex" || true)
    if [ -n "$hit" ]; then
        violations+="违规规则：$rule
$hit

"
    fi
}

# 规则 0：api 接口层——只允许触达编排/检索/业务(只读展示)/支撑域，禁止绕过编排直接调 llm。
allowed_api='gewu/internal/(agent|rag|business|budget|config|middleware|dates)\b'
hit=$(go list -f '{{.Imports}}' ./internal/api | grep -oE "gewu/internal/[a-z_]+" | sort -u | grep -vE "$allowed_api" || true)
if [ -n "$hit" ]; then
    violations+="违规规则：api 只 import 接口层所需包（agent/rag/business/支撑域），不得直接调 llm
$hit

"
fi

# 规则 1：rag / llm / business 反向禁止 import agent。
forbid "./internal/rag/..."      "internal/agent\b" "rag ↛ agent（rag/llm/business 不得 import 编排域）"
forbid "./internal/llm/..."      "internal/agent\b" "llm ↛ agent（rag/llm/business 不得 import 编排域）"
forbid "./internal/business/..." "internal/agent\b" "business ↛ agent（业务域只经 agent.Tools 被触达，不得反向 import）"

# 规则 2：business 不 import rag（业务域与检索域互不依赖）。
forbid "./internal/business/..." "internal/rag\b" "business ↛ rag（业务域不依赖检索域）"

# 规则 3：支撑域不 import 任何业务/编排/检索/模型域。
for sup in budget config dates middleware; do
    forbid "./internal/$sup/..." "internal/(agent|rag|business|llm)\b" "支撑域 $sup 不得 import 业务域（agent/rag/business/llm）"
done

# 规则 4：cmd/server 只做装配——仅允许白名单内的 internal 包。
allowed='gewu/internal/(api|agent|rag|llm|business|config|budget|middleware|dates)\b'
hit=$(deps ./cmd/server | grep -oE "gewu/internal/[a-z_]+" | sort -u | grep -vE "$allowed" || true)
if [ -n "$hit" ]; then
    violations+="违规规则：cmd/server 只 import 装配白名单（api/agent/rag/llm/business/config/budget/middleware/dates）
$hit

"
fi

if [ -n "$violations" ]; then
    echo "lint-arch 发现依赖规则违规：
$violations"
    exit 1
fi
echo "lint-arch：依赖规则全部通过（agent→rag/llm/business 单向；支撑域无业务依赖；cmd/server 仅装配）"
