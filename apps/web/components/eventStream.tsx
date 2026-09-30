"use client";

import type { ActionResult, Citation, DoneReason, PendingAction } from "@/lib/api";
import { DONE_BADGE, ROUTE_LABEL } from "@/lib/labels";

/** 时间线条目：status / step / slot_question / action_result 的顺序化呈现。 */
export interface TimelineItem {
  kind: "status" | "step" | "slot" | "result";
  text: string;
  detail?: string;
}

/** 一轨一轮的完整状态（compare 页维护，本组件只读渲染）。 */
export interface TrackRound {
  question: string;
  route?: string;
  reason?: string;
  timeline: TimelineItem[];
  answer: string;
  citations: Citation[];
  pendingAction?: PendingAction;
  actionResult?: ActionResult;
  error?: string;
  done: boolean;
  latency?: number;
  doneReason?: DoneReason;
  eventCount: number;
}

const KIND_ICON: Record<TimelineItem["kind"], string> = {
  status: "·",
  step: "？",
  slot: "▢",
  result: "→",
};

/** ReAct 引擎的工具调用只经 status 文本（`调用工具 X…`）可见，渲染为工具 chip。 */
const TOOL_RE = /^调用工具 (\S+?)…$/;

function TimelineRow({ item }: { item: TimelineItem }) {
  const tool = item.kind === "status" ? item.text.match(TOOL_RE)?.[1] : undefined;
  return (
    <li className={`tl-item ${item.kind}`}>
      <span className="tl-icon" aria-hidden>
        {KIND_ICON[item.kind]}
      </span>
      {tool ? (
        <span className="tool-chip" title="ReAct 工具调用">
          🔧 {tool}
        </span>
      ) : (
        <span className="tl-text">{item.text}</span>
      )}
      {item.detail && <span className="tl-detail">{item.detail}</span>}
    </li>
  );
}

/**
 * 对比实验台的单轨面板：标题 + 路由判定 + 事件时间线 + 答案 + 引用 + 确认流 + 结束元信息。
 */
export default function TrackPanel({
  tag,
  title,
  note,
  round,
  busy,
  onConfirm,
  onCancel,
}: {
  tag: string;
  title: string;
  note?: string;
  round?: TrackRound;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <section className="track" aria-label={title}>
      <header className="track-head">
        <span className="track-tag">{tag}</span>
        <div>
          <h2>{title}</h2>
          {note && <p className="track-note">{note}</p>}
        </div>
        {round?.route && (
          <span className={`badge ${round.route}`} title={round.reason} style={{ marginLeft: "auto" }}>
            {ROUTE_LABEL[round.route] ?? round.route}
          </span>
        )}
      </header>

      {!round && !busy && <p className="track-empty">等待提问…</p>}
      {round && (
        <>
          <p className="round-q">{round.question}</p>
          {round.timeline.length > 0 && (
            <ol className="timeline">
              {round.timeline.map((item, i) => (
                <TimelineRow key={i} item={item} />
              ))}
            </ol>
          )}
          {!round.done && busy && <p className="status">运行中…</p>}
          {round.answer && <p className="answer">{round.answer}</p>}

          {round.pendingAction && !round.actionResult && round.done && (
            <div className="confirm-card">
              <div className="confirm-title">待确认 · {round.pendingAction.label}</div>
              <dl className="args">
                {Object.entries(round.pendingAction.args).map(([k, v]) => (
                  <div key={k}>
                    <dt>{k}</dt>
                    <dd>{v}</dd>
                  </div>
                ))}
              </dl>
              <div className="confirm-buttons">
                <button className="primary" onClick={onConfirm} disabled={busy}>
                  确认办理（本轨）
                </button>
                <button onClick={onCancel} disabled={busy}>
                  取消
                </button>
              </div>
            </div>
          )}

          {round.actionResult && (
            <div className={`receipt ${round.actionResult.success ? "ok" : "fail"}`}>
              <span>{round.actionResult.success ? "✔" : "✖"}</span>
              <span>
                {round.actionResult.message}
                {round.actionResult.success && round.actionResult.receipt
                  ? `（凭证号 ${round.actionResult.receipt}）`
                  : ""}
              </span>
            </div>
          )}

          {round.citations.length > 0 && (
            <div className="citations">
              <span className="cite-title">引用</span>
              {round.citations.map((c) => (
                <span key={c.n} className="cite-chip" title={c.doc_id}>
                  [{c.n}] {c.title}
                </span>
              ))}
            </div>
          )}
          {round.error && <p className="error">出错了：{round.error}</p>}
          {round.done && (
            <span className="meta">
              <span className="latency">{round.latency ?? 0} ms</span>
              <span className="latency">· {round.eventCount} 事件</span>
              {round.doneReason &&
                DONE_BADGE[round.doneReason] &&
                (() => {
                  const b = DONE_BADGE[round.doneReason]!;
                  return <span className={`done-badge ${b.cls}`}>{b.text}</span>;
                })()}
            </span>
          )}
        </>
      )}
    </section>
  );
}
