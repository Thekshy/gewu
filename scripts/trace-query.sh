#!/usr/bin/env bash
# trace-query（P27-3）：第一方链路追踪的 CLI 消费面——第一消费者是 AI 排障
# （ssh + psql 母语），本地直跑 / 线上沿 log-report.sh 的 ssh 透传模式。
#
# 用法：trace-query.sh <action> [args]
#   latest [N]        最近 N 轮（默认 20）
#   session <sid> [N] 某会话最近 N 轮
#   find <关键词>      按问题关键词定位轮（ILIKE）
#   spans <trace_id>  某轮 span 明细（seq/kind/name/status/ms/tokens + input/output）
#   errors [N]        最近 error 轮（问题台账）
#   stats             按 route 聚合：轮数/p50/p95 延迟/error 数/平均 span 数
set -u
cd "$(dirname "$0")/.."

ACTION="${1:-latest}"

# PG_DSN：.env 优先（与 gewu config 同锚点），缺省本地 docker 口径
DSN="${PG_DSN:-}"
if [ -z "$DSN" ] && [ -f .env ]; then
  DSN=$(grep -E '^PG_DSN=' .env | tail -1 | cut -d= -f2-)
fi
DSN="${DSN:-postgres://gewu:gewu@127.0.0.1:5433/gewu?sslmode=disable}"

PSQL=(psql "$DSN" -v ON_ERROR_STOP=1 --no-psqlrc -P pager=off)

case "$ACTION" in
  latest)
    N="${2:-20}"
    "${PSQL[@]}" -c "SELECT id, to_char(created_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI:SS') AS at,
        session_id, mode, route, reason, latency_ms AS ms, steps,
        left(question, 24) AS question, left(answer_head, 20) AS answer_head, error
      FROM agent_trace ORDER BY id DESC LIMIT $N" ;;
  session)
    SID="${2:?用法: session <sid> [N]}"; N="${3:-10}"
    "${PSQL[@]}" -c "SELECT id, to_char(created_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI:SS') AS at,
        mode, route, reason, latency_ms AS ms, steps,
        left(question, 30) AS question, left(answer_head, 24) AS answer_head, error
      FROM agent_trace WHERE session_id = '$SID' ORDER BY id DESC LIMIT $N" ;;
  find)
    KW="${2:?用法: find <关键词>}"
    "${PSQL[@]}" -c "SELECT id, to_char(created_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI') AS at,
        session_id, mode, route, latency_ms AS ms,
        left(question, 40) AS question, left(coalesce(error, ''), 30) AS error
      FROM agent_trace WHERE question ILIKE '%$KW%' ORDER BY id DESC LIMIT 20" ;;
  spans)
    TID="${2:?用法: spans <trace_id>}"
    "${PSQL[@]}" -c "SELECT seq, kind, name, status, latency_ms AS ms, tokens,
        coalesce(input::text, '') AS input, coalesce(output::text, '') AS output
      FROM agent_span WHERE trace_id = $TID ORDER BY seq" ;;
  errors)
    N="${2:-10}"
    "${PSQL[@]}" -c "SELECT id, to_char(created_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI:SS') AS at,
        session_id, route, reason, latency_ms AS ms,
        left(question, 24) AS question, left(error, 60) AS error
      FROM agent_trace WHERE error IS NOT NULL ORDER BY id DESC LIMIT $N" ;;
  stats)
    "${PSQL[@]}" -c "SELECT route, count(*) AS turns,
        percentile_disc(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
        percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
        count(*) FILTER (WHERE error IS NOT NULL) AS errors,
        round(avg(span_n), 1) AS avg_spans
      FROM agent_trace t
      LEFT JOIN LATERAL (SELECT count(*) AS span_n FROM agent_span s WHERE s.trace_id = t.id) s ON true
      GROUP BY route ORDER BY turns DESC" ;;
  *)
    echo "未知动作：$ACTION（可用：latest/session/find/spans/errors/stats）" >&2
    exit 1 ;;
esac
