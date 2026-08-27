"use client";

import { useEffect, useRef, useState } from "react";
import {
  API_BASE,
  fetchHealth,
  streamChat,
  type ChatEvent,
  type Citation,
  type HealthInfo,
  type Step,
} from "@/lib/api";

type Mode = "auto" | "direct" | "research";

interface Msg {
  role: "user" | "assistant";
  text: string;
  route?: string;
  reason?: string;
  status?: string;
  steps: Step[];
  citations: Citation[];
  latency?: number;
  error?: string;
  done: boolean;
}

const ROUTE_LABEL: Record<string, string> = {
  factual: "直答",
  research: "深度研究",
  refusal: "范围外",
};

const SUGGESTIONS = [
  "转专业之后原课程绩点还算吗？会影响保研吗？",
  "想申请国奖但体测没过，有补救办法吗？",
  "图书馆一次能借几本书、借多久？",
];

export default function Home() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<Mode>("auto");
  const [sending, setSending] = useState(false);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchHealth().then(setHealth);
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function patchLast(patch: Partial<Msg> | ((m: Msg) => Msg)) {
    setMessages((prev) => {
      if (prev.length === 0) return prev;
      const next = [...prev];
      const i = next.length - 1;
      next[i] = typeof patch === "function" ? patch(next[i]) : { ...next[i], ...patch };
      return next;
    });
  }

  async function send(text?: string) {
    const question = (text ?? input).trim();
    if (!question || sending) return;
    setInput("");
    setSending(true);
    setMessages((prev) => [
      ...prev,
      { role: "user", text: question, steps: [], citations: [], done: true },
      { role: "assistant", text: "", steps: [], citations: [], done: false },
    ]);
    try {
      await streamChat(question, mode, (ev: ChatEvent) => {
        switch (ev.type) {
          case "route":
            patchLast({ route: String(ev.route), reason: String(ev.reason ?? "") });
            break;
          case "status":
            patchLast({ status: String(ev.text ?? "") });
            break;
          case "step":
            patchLast((m) => ({ ...m, steps: [...m.steps, ev as unknown as Step] }));
            break;
          case "answer_delta":
            patchLast((m) => ({ ...m, text: m.text + String(ev.text ?? "") }));
            break;
          case "citations":
            patchLast({ citations: (ev.items as Citation[]) ?? [] });
            break;
          case "error":
            patchLast({ error: String(ev.message ?? "未知错误") });
            break;
          case "done":
            patchLast({ done: true, latency: Number(ev.latency_ms ?? 0), status: undefined });
            break;
        }
      });
    } catch (err) {
      patchLast({ error: err instanceof Error ? err.message : String(err), done: true });
    } finally {
      setSending(false);
    }
  }

  return (
    <main className="page">
      <header className="header">
        <div className="logo" aria-hidden>
          格
        </div>
        <div>
          <h1>格物</h1>
          <p className="tagline">
            钱塘大学校园智能问答 · RAG 直答 × Deep Research
            {health && (
              <span className="stat">
                {health.docs} 篇文档 / {health.chunks} chunks
              </span>
            )}
          </p>
        </div>
      </header>

      {health && !health.llm && (
        <div className="banner">
          检索演示模式：未配置 LLM_API_KEY，回答为知识库检索节选，配置后即可体验完整问答。
        </div>
      )}
      {!health && <div className="banner warn">连不上后端（{API_BASE}），请先启动 API 服务。</div>}

      <section className="chat" aria-label="对话区">
        {messages.length === 0 && (
          <div className="empty">
            <p>问问校园里的事，比如转专业、保研、奖学金、图书馆、校历……</p>
            <p className="hint">复合问题会自动进入深度研究链路：拆解 → 多路检索 → 交叉综合。</p>
          </div>
        )}

        {messages.map((msg, i) =>
          msg.role === "user" ? (
            <div key={i} className="row user">
              <div className="bubble user">{msg.text}</div>
            </div>
          ) : (
            <div key={i} className="row assistant">
              <div className="bubble assistant">
                {msg.route && (
                  <span className={`badge ${msg.route}`} title={msg.reason}>
                    {ROUTE_LABEL[msg.route] ?? msg.route}
                  </span>
                )}
                {msg.steps.length > 0 && (
                  <details className="trace" open={!msg.done}>
                    <summary>研究过程 · {msg.steps.length} 个子问题</summary>
                    <ol>
                      {msg.steps.map((s) => (
                        <li key={s.index}>
                          <span className="subq">{s.subquestion}</span>
                          {s.sources.length > 0 && (
                            <span className="src"> ↳ {s.sources.join("、")}</span>
                          )}
                        </li>
                      ))}
                    </ol>
                  </details>
                )}
                {msg.status && <p className="status">{msg.status}</p>}
                {msg.text && <p className="answer">{msg.text}</p>}
                {!msg.text && !msg.status && msg.steps.length === 0 && !msg.done && (
                  <p className="status">思考中…</p>
                )}
                {msg.citations.length > 0 && (
                  <div className="citations">
                    <span className="cite-title">引用来源</span>
                    {msg.citations.map((c) => (
                      <span key={c.n} className="cite-chip" title={c.doc_id}>
                        [{c.n}] {c.title} · {c.source}
                      </span>
                    ))}
                  </div>
                )}
                {msg.error && <p className="error">出错了：{msg.error}</p>}
                {msg.done && msg.latency != null && <span className="latency">{msg.latency} ms</span>}
              </div>
            </div>
          ),
        )}
        <div ref={bottomRef} />
      </section>

      <div className="composer">
        <div className="suggestions">
          {SUGGESTIONS.map((s) => (
            <button key={s} className="chip" onClick={() => send(s)} disabled={sending}>
              {s}
            </button>
          ))}
        </div>
        <div className="inputbar">
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as Mode)}
            aria-label="回答模式"
          >
            <option value="auto">自动路由</option>
            <option value="direct">强制直答</option>
            <option value="research">强制研究</option>
          </select>
          <textarea
            value={input}
            placeholder="输入你的问题，Enter 发送，Shift+Enter 换行"
            rows={1}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
          />
          <button onClick={() => send()} disabled={sending || !input.trim()}>
            {sending ? "回答中…" : "发送"}
          </button>
        </div>
      </div>

      <footer className="footer">
        演示语料为虚构的「钱塘大学」合成数据，与任何真实高校无关 ·
        格物 Gewu 是开源的个人求职展示项目
      </footer>
    </main>
  );
}
