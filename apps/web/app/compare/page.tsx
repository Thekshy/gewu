"use client";

import { useRef, useState } from "react";
import TrackPanel, { type TimelineItem, type TrackRound } from "@/components/eventStream";
import { streamChat, type ChatEvent, type ChatMode, type Role } from "@/lib/api";
import { SLOT_LABEL } from "@/lib/labels";

// 对比实验台：同题并发打两条链路——A 轨 mode=auto（级联路由 + 固定 workflow）、
// B 轨 mode=react（ReAct 引擎自主组合工具）。两轨 session 隔离：办理流程的槽位/确认
// 状态各自独立，B 轨的确认不污染 A 轨。B 轨的 route 事件是级联判定（pipeline 仍先发
// route 再进 ReAct），仅作参考，实际执行以工具时间线为准。

const SUGGESTIONS = [
  "帮我预约明天晚上 19:00-21:00 的羽毛球馆，班级比赛用",
  "转专业之后原课程绩点还算吗？会影响保研吗？",
  "帮我请下周一到下周二的事假，另外超过 7 天是不是要教务处批？",
];

type Side = "a" | "b";

interface TrackState {
  busy: boolean;
  rounds: TrackRound[];
}

function newRound(question: string): TrackRound {
  return { question, timeline: [], answer: "", citations: [], done: false, eventCount: 0 };
}

function uuid(): string {
  return typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `s-${Date.now()}`;
}

export default function Compare() {
  const [input, setInput] = useState("");
  const [role, setRole] = useState<Role>("student");
  const [a, setA] = useState<TrackState>({ busy: false, rounds: [] });
  const [b, setB] = useState<TrackState>({ busy: false, rounds: [] });
  // 会话隔离的关键：两轨固定独立 session_id（useRef 跨渲染稳定），跨轮复用保持各自多轮上下文
  const sessions = useRef<{ a: string; b: string }>({
    a: `compare-a-${uuid()}`,
    b: `compare-b-${uuid()}`,
  });

  const running = a.busy || b.busy;
  const lastRound = (t: TrackState) => t.rounds[t.rounds.length - 1];

  function setBusy(side: Side, busy: boolean) {
    (side === "a" ? setA : setB)((prev) => ({ ...prev, busy }));
  }

  function pushRound(side: Side, round: TrackRound) {
    (side === "a" ? setA : setB)((prev) => ({ ...prev, rounds: [...prev.rounds, round] }));
  }

  function patchRound(side: Side, patch: Partial<TrackRound> | ((r: TrackRound) => TrackRound)) {
    (side === "a" ? setA : setB)((prev) => {
      if (prev.rounds.length === 0) return prev;
      const rounds = [...prev.rounds];
      const i = rounds.length - 1;
      rounds[i] = typeof patch === "function" ? patch(rounds[i]) : { ...rounds[i], ...patch };
      return { ...prev, rounds };
    });
  }

  function pushItem(side: Side, item: TimelineItem) {
    patchRound(side, (r) => ({ ...r, timeline: [...r.timeline, item] }));
  }

  async function run(side: Side, question: string) {
    const mode: ChatMode = side === "a" ? "auto" : "react";
    setBusy(side, true);
    pushRound(side, newRound(question));
    try {
      await streamChat(
        question,
        mode,
        (ev: ChatEvent) => handleEvent(side, ev),
        { sessionId: sessions.current[side], role },
      );
    } catch (err) {
      patchRound(side, { error: err instanceof Error ? err.message : String(err), done: true });
    } finally {
      setBusy(side, false);
    }
  }

  function handleEvent(side: Side, ev: ChatEvent) {
    patchRound(side, (r) => ({ ...r, eventCount: r.eventCount + 1 }));
    switch (ev.type) {
      case "route":
        patchRound(side, { route: String(ev.route), reason: String(ev.reason ?? "") });
        break;
      case "status":
        pushItem(side, { kind: "status", text: String(ev.text ?? "") });
        break;
      case "step":
        pushItem(side, {
          kind: "step",
          text: String(ev.subquestion ?? ""),
          detail: ((ev.sources as string[]) ?? []).join("、"),
        });
        break;
      case "slot_question":
        pushItem(side, {
          kind: "slot",
          text: `追问：${SLOT_LABEL[String(ev.slot)] ?? String(ev.slot)}`,
        });
        break;
      case "answer_delta":
        patchRound(side, (r) => ({ ...r, answer: r.answer + String(ev.text ?? "") }));
        break;
      case "citations":
        patchRound(side, { citations: (ev.items as TrackRound["citations"]) ?? [] });
        break;
      case "pending_action":
        patchRound(side, {
          pendingAction: {
            tool: String(ev.tool),
            label: String(ev.label),
            args: (ev.args as Record<string, string>) ?? {},
          },
        });
        break;
      case "action_result":
        patchRound(side, {
          actionResult: {
            tool: String(ev.tool),
            success: Boolean(ev.success),
            message: String(ev.message ?? ""),
            receipt: (ev.receipt as string | null) ?? null,
          },
        });
        pushItem(side, {
          kind: "result",
          text: `${Boolean(ev.success) ? "执行成功" : "执行失败"} · ${String(ev.tool)}`,
        });
        break;
      case "error":
        patchRound(side, { error: String(ev.message ?? "未知错误") });
        break;
      case "done":
        patchRound(side, {
          done: true,
          latency: Number(ev.latency_ms ?? 0),
          doneReason: (ev.reason as TrackRound["doneReason"]) || "completed",
        });
        break;
    }
  }

  function send(text?: string) {
    const question = (text ?? input).trim();
    if (!question || running) return;
    setInput("");
    void run("a", question);
    void run("b", question);
  }

  const ra = lastRound(a);
  const rb = lastRound(b);
  const bothDone = ra?.done && rb?.done && !running;

  return (
    <main className="page wide">
      <header className="header">
        <div className="logo" aria-hidden>
          较
        </div>
        <div className="header-main">
          <h1>对比实验台</h1>
          <p className="tagline">
            同一问题并发两条链路：级联路由 + 固定 workflow ↔ ReAct 自主组合工具
          </p>
        </div>
        <select
          className="role-select"
          value={role}
          onChange={(e) => setRole(e.target.value as Role)}
          title="演示身份（权限不同）"
        >
          <option value="student">学生身份</option>
          <option value="counselor">辅导员身份</option>
        </select>
      </header>

      <div className="inputbar compare-bar">
        <textarea
          value={input}
          placeholder="输入问题，Enter 同题双发，Shift+Enter 换行"
          rows={1}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button onClick={() => send()} disabled={running || !input.trim()}>
          {running ? "运行中…" : "同题双发"}
        </button>
      </div>
      <div className="suggestions">
        {SUGGESTIONS.map((s) => (
          <button key={s} className="chip" onClick={() => send(s)} disabled={running}>
            {s}
          </button>
        ))}
      </div>

      <div className="tracks">
        <TrackPanel
          tag="A"
          title="级联路由 + 固定 workflow"
          note="mode=auto：L0 规则快路径 → L1 小模型五分类 → L2 主模型复核；路由决定后续固定链路"
          round={ra}
          busy={a.busy}
          onConfirm={() => void run("a", "确认")}
          onCancel={() => void run("a", "取消")}
        />
        <TrackPanel
          tag="B"
          title="ReAct 自主组合工具"
          note="mode=react：路由事件仅参考，实际由模型每轮自主决定调用哪个工具（🔧），写操作仍走确认流"
          round={rb}
          busy={b.busy}
          onConfirm={() => void run("b", "确认")}
          onCancel={() => void run("b", "取消")}
        />
      </div>

      {bothDone && ra && rb && (
        <section className="diff" aria-label="两轨差异摘要">
          <h2>本轮差异</h2>
          <table>
            <thead>
              <tr>
                <th></th>
                <th>A · 级联 workflow</th>
                <th>B · ReAct</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th>路由判定</th>
                <td title={ra.reason}>
                  {ra.route} · {ra.reason}
                </td>
                <td title={rb.reason}>
                  {rb.route}（参考） · 实际 ReAct
                </td>
              </tr>
              <tr>
                <th>事件数</th>
                <td>{ra.eventCount}</td>
                <td>{rb.eventCount}</td>
              </tr>
              <tr>
                <th>耗时</th>
                <td>{ra.latency ?? 0} ms</td>
                <td>{rb.latency ?? 0} ms</td>
              </tr>
              <tr>
                <th>引用来源</th>
                <td>{ra.citations.length} 条</td>
                <td>{rb.citations.length} 条</td>
              </tr>
            </tbody>
          </table>
          <p className="diff-note">前端只呈现事实；同数据集的量化对比见 eval/reports/。</p>
        </section>
      )}

      <footer className="footer">
        两轨会话隔离（独立 session_id），B 轨确认办理不影响 A 轨 ·
        演示语料为虚构「钱塘大学」合成数据
      </footer>
    </main>
  );
}
