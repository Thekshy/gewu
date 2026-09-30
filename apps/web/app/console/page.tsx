"use client";

import { useCallback, useEffect, useState } from "react";
import {
  API_BASE,
  businessReset,
  fetchBusinessOverview,
  fetchDocs,
  fetchHealth,
  search,
  type BusinessOverview,
  type DocInfo,
  type HealthInfo,
  type SearchHit,
} from "@/lib/api";

// 演示控制台：业务台账（交易真实落库的证据 + 演示重置）/ 检索调试（不经 LLM 直接看
// 混合检索命中）/ 语料列表 / 服务健康与预算。全部只读既有 API，零后端改动。

function Panel({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>{title}</h2>
        {note && <p className="panel-note">{note}</p>}
      </header>
      {children}
    </section>
  );
}

function Health({ health }: { health: HealthInfo | null }) {
  if (!health) return <p className="track-empty">连不上后端（{API_BASE}）</p>;
  const pct = health.budget.limit > 0 ? Math.min(100, (health.budget.used / health.budget.limit) * 100) : 0;
  return (
    <div className="health">
      <div className="health-row">
        <span className={`dot ${health.llm ? "ok" : "off"}`} />
        LLM {health.llm ? "已配置" : "未配置（检索演示模式）"}
        <span className={`dot ${health.embeddings ? "ok" : "off"}`} />
        向量 {health.embeddings ? "启用" : "关闭"}
        <span className="health-ver">v{health.version}</span>
      </div>
      <div className="health-row">
        语料 {health.docs} 篇 / {health.chunks} chunks
      </div>
      <div className="health-row">
        今日 token 预算 {health.budget.used.toLocaleString()} / {health.budget.limit.toLocaleString()}
        <span className="bar">
          <span className="bar-fill" style={{ width: `${pct}%` }} />
        </span>
        {pct.toFixed(1)}%
      </div>
    </div>
  );
}

function Ledger() {
  const [data, setData] = useState<BusinessOverview | null>(null);
  const [err, setErr] = useState("");
  const [confirming, setConfirming] = useState(false);

  const load = useCallback(() => {
    fetchBusinessOverview()
      .then(setData)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(load, [load]);

  async function reset() {
    try {
      await businessReset();
      setConfirming(false);
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <>
      <div className="panel-actions">
        <button className="chip" onClick={load}>
          刷新
        </button>
        {confirming ? (
          <>
            <button className="danger" onClick={() => void reset()}>
              确认清空业务数据
            </button>
            <button className="chip" onClick={() => setConfirming(false)}>
              取消
            </button>
          </>
        ) : (
          <button className="chip" onClick={() => setConfirming(true)}>
            演示重置
          </button>
        )}
      </div>
      {err && <p className="error">出错了：{err}</p>}
      {data && (
        <div className="ledger">
          <h3>场馆预约（{data.bookings.length}）</h3>
          {data.bookings.length === 0 ? (
            <p className="empty-line">暂无预约</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>单号</th>
                  <th>场馆</th>
                  <th>日期</th>
                  <th>时段</th>
                  <th>用户</th>
                </tr>
              </thead>
              <tbody>
                {data.bookings.map((bk) => (
                  <tr key={bk.booking_id}>
                    <td className="mono">{bk.booking_id}</td>
                    <td>{bk.venue}</td>
                    <td>{bk.date}</td>
                    <td>{bk.slot}</td>
                    <td>{bk.user}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <h3>请假单（{data.tickets.length}）</h3>
          {data.tickets.length === 0 ? (
            <p className="empty-line">暂无请假单</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>单号</th>
                  <th>类型</th>
                  <th>起止</th>
                  <th>天数</th>
                  <th>审批</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {data.tickets.map((t) => (
                  <tr key={t.ticket}>
                    <td className="mono">{t.ticket}</td>
                    <td>{t.leave_type}</td>
                    <td>
                      {t.start} ~ {t.end}
                    </td>
                    <td>{t.days}</td>
                    <td>{t.approver}</td>
                    <td>{t.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </>
  );
}

function SearchBench() {
  const [query, setQuery] = useState("转专业绩点要求");
  const [k, setK] = useState(5);
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function go() {
    if (!query.trim() || busy) return;
    setBusy(true);
    setErr("");
    try {
      setHits(await search(query.trim(), k));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setHits(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="panel-actions">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void go()}
          placeholder="检索词（1~200 字）"
          aria-label="检索词"
        />
        <select value={k} onChange={(e) => setK(Number(e.target.value))} aria-label="top-k">
          {[3, 5, 8, 10].map((n) => (
            <option key={n} value={n}>
              top {n}
            </option>
          ))}
        </select>
        <button onClick={() => void go()} disabled={busy || !query.trim()}>
          {busy ? "检索中…" : "检索"}
        </button>
      </div>
      {err && <p className="error">出错了：{err}</p>}
      {hits && (
        <ol className="hits">
          {hits.map((h, i) => (
            <li key={`${h.doc_id}-${h.seq}`}>
              <div className="hit-head">
                <span className="hit-rank">#{i + 1}</span>
                <span className="hit-title">{h.title}</span>
                <span className="hit-meta">
                  {h.source} · 块 {h.seq}
                </span>
              </div>
              <p className="hit-text">{h.text}</p>
            </li>
          ))}
        </ol>
      )}
    </>
  );
}

function Corpus() {
  const [docs, setDocs] = useState<DocInfo[] | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    fetchDocs()
      .then(setDocs)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  if (err) return <p className="error">出错了：{err}</p>;
  if (!docs) return <p className="empty-line">加载中…</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>标题</th>
          <th>来源</th>
          <th>更新</th>
          <th>chunks</th>
        </tr>
      </thead>
      <tbody>
        {docs.map((d) => (
          <tr key={d.doc_id}>
            <td>{d.title}</td>
            <td>{d.source}</td>
            <td>{d.updated}</td>
            <td>{d.chunks}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Console() {
  const [health, setHealth] = useState<HealthInfo | null>(null);
  useEffect(() => {
    fetchHealth().then(setHealth);
  }, []);

  return (
    <main className="page wide">
      <header className="header">
        <div className="logo" aria-hidden>
          台
        </div>
        <div className="header-main">
          <h1>演示控制台</h1>
          <p className="tagline">业务台账 · 检索调试 · 语料 · 服务健康——对话页之外的全部调试入口</p>
        </div>
      </header>

      <div className="console-grid">
        <Panel title="服务健康">
          <Health health={health} />
        </Panel>
        <Panel title="业务台账" note="办理确认后真实落库（SQLite business.db）；演示前可一键重置">
          <Ledger />
        </Panel>
        <Panel title="检索调试" note="直接调 /api/search：BM25 + 向量 RRF 混合命中，不经 LLM">
          <SearchBench />
        </Panel>
        <Panel title="语料" note="GET /api/docs：已入库的虚构「钱塘大学」政策文档">
          <Corpus />
        </Panel>
      </div>

      <footer className="footer">全部数据来自只读/调试 API · 格物 Gewu 求职展示项目</footer>
    </main>
  );
}
