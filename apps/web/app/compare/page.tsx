"use client";

import { useRef, useState } from "react";
import { Loader2, Send, TriangleAlert } from "lucide-react";
import TrackPanel, { type TimelineItem, type TrackRound } from "@/components/eventStream";
import {
  createSession,
  streamChat,
  type ChatEvent,
  type ChatMode,
  type SessionInfo,
} from "@/lib/api";
import { useRequireUser } from "@/lib/auth";
import { SLOT_LABEL } from "@/lib/labels";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

// 对比实验台：同题并发打两条链路——A 轨 mode=auto（P17 起=agent-first 单循环：
// guard 安检 + create_agent 工具自选）、B 轨 mode=classic（级联路由 + 固定 workflow，
// 论文对照基线）。两轨 session 隔离：办理流程的槽位/确认状态各自独立。A 轨 route
// 事件两段式（guard provisional + effective 工具轨迹合成），以 effective 为准。
// P22：会话改服务端下发（kind=compare 不入对话侧栏），首跑前 ensure、失败即停双发。

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

export default function Compare() {
  useRequireUser(); // P21：登录守卫（role 由服务端随会话下发）
  const [input, setInput] = useState("");
  const [a, setA] = useState<TrackState>({ busy: false, rounds: [] });
  const [b, setB] = useState<TrackState>({ busy: false, rounds: [] });
  // 会话隔离的关键：两轨各自独立 compare 会话（P22 起服务端 POST /api/sessions 下发，
  // useRef 缓存创建结果——首个 Promise 落定前不重复建），跨轮复用保持各自多轮上下文
  const sessions = useRef<Record<Side, SessionInfo | null>>({ a: null, b: null });
  const [sessionErr, setSessionErr] = useState<string | null>(null);

  async function ensureSession(side: Side): Promise<SessionInfo> {
    if (!sessions.current[side]) {
      sessions.current[side] = await createSession("compare");
    }
    return sessions.current[side];
  }

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

  async function run(side: Side, question: string, session: SessionInfo) {
    const mode: ChatMode = side === "a" ? "auto" : "classic";
    setBusy(side, true);
    pushRound(side, newRound(question));
    try {
      await streamChat(
        question,
        mode,
        (ev: ChatEvent) => handleEvent(side, ev),
        { sessionId: session.session_id },
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
      case "answer_reset":
        // P30：流式中间轮撤回——已显示文本转存为一条 step 后清空
        patchRound(side, (r) =>
          r.answer
            ? {
                ...r,
                timeline: [...r.timeline, { kind: "step" as const, text: r.answer }],
                answer: "",
              }
            : r,
        );
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

  async function send(text?: string) {
    const question = (text ?? input).trim();
    if (!question || running) return;
    setSessionErr(null);
    // 双轨会话先 ensure（首跑前创建，失败即报错停止双发——任务书 Q 风险表）
    let sa: SessionInfo, sb: SessionInfo;
    try {
      [sa, sb] = await Promise.all([ensureSession("a"), ensureSession("b")]);
    } catch (err) {
      setSessionErr(err instanceof Error ? err.message : String(err));
      return;
    }
    setInput("");
    void run("a", question, sa);
    void run("b", question, sb);
  }

  /** 确认门续轮（确认/取消）：复用已创建的轨会话。 */
  function reply(side: Side, text: string) {
    const s = sessions.current[side];
    if (s) void run(side, text, s);
  }

  const ra = lastRound(a);
  const rb = lastRound(b);
  const bothDone = ra?.done && rb?.done && !running;

  return (
    <main className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-6xl space-y-5 px-4 py-6">
        <header className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-semibold">对比实验台</h1>
            <p className="text-sm text-muted-foreground">
              同一问题并发两条链路：agent-first 单循环 ↔ 级联路由固定 workflow（论文双底座对照）
            </p>
          </div>
        </header>

        <div className="space-y-2">
          <div className="flex items-end gap-2 rounded-2xl border bg-card p-2 shadow-sm transition-colors focus-within:border-primary/50 focus-within:ring-4 focus-within:ring-primary/10">
            <textarea
              value={input}
              placeholder="输入问题，Enter 同题双发，Shift+Enter 换行"
              rows={1}
              className="max-h-40 min-h-9 flex-1 resize-none border-0 bg-transparent px-2 py-2 text-sm outline-none placeholder:text-muted-foreground"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  send();
                }
              }}
            />
            <Button onClick={() => send()} disabled={running || !input.trim()} className="h-9 shrink-0">
              {running ? <Loader2 className="animate-spin" aria-hidden /> : <Send aria-hidden />}
              {running ? "运行中…" : "同题双发"}
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => send(s)}
                disabled={running}
                className="rounded-full border bg-card px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
              >
                {s}
              </button>
            ))}
          </div>
        </div>

        {sessionErr && (
          <Alert variant="destructive" className="py-2.5">
            <TriangleAlert className="size-4" aria-hidden />
            <AlertDescription>
              会话创建失败：{sessionErr}
              <button className="ml-2 underline underline-offset-2" onClick={() => setSessionErr(null)}>
                关闭
              </button>
            </AlertDescription>
          </Alert>
        )}

        <div className="grid gap-4 md:grid-cols-2">
          <TrackPanel
            tag="A"
            title="Agent-first 单循环（主路）"
            note="mode=auto：guard 安检 + create_agent 单循环，模型每轮自主选工具；写操作仍走确认门"
            round={ra}
            busy={a.busy}
            onConfirm={() => reply("a", "确认")}
            onCancel={() => reply("a", "取消")}
          />
          <TrackPanel
            tag="B"
            title="级联路由 + 固定 workflow（对照基线）"
            note="mode=classic：L0 规则快路径 → L1 小模型五分类 → L2 主模型复核；路由决定后续固定链路"
            round={rb}
            busy={b.busy}
            onConfirm={() => reply("b", "确认")}
            onCancel={() => reply("b", "取消")}
          />
        </div>

        {bothDone && ra && rb && (
          <Card aria-label="两轨差异摘要">
            <CardHeader>
              <CardTitle className="text-sm">本轮差异</CardTitle>
              <CardDescription>前端只呈现事实；同数据集的量化对比见 eval/reports/。</CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-28"></TableHead>
                    <TableHead>A · agent-first 循环</TableHead>
                    <TableHead>B · classic 级联</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  <TableRow>
                    <TableCell className="font-medium">路由判定</TableCell>
                    <TableCell title={ra.reason}>
                      {ra.route} · {ra.reason}
                    </TableCell>
                    <TableCell title={rb.reason}>
                      {rb.route} · 级联判定决定固定链路
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-medium">事件数</TableCell>
                    <TableCell className="tabular-nums">{ra.eventCount}</TableCell>
                    <TableCell className="tabular-nums">{rb.eventCount}</TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-medium">耗时</TableCell>
                    <TableCell className="tabular-nums">{ra.latency ?? 0} ms</TableCell>
                    <TableCell className="tabular-nums">{rb.latency ?? 0} ms</TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-medium">引用来源</TableCell>
                    <TableCell className="tabular-nums">{ra.citations.length} 条</TableCell>
                    <TableCell className="tabular-nums">{rb.citations.length} 条</TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        )}

        <footer className="pb-4 text-center text-[11px] text-muted-foreground">
          两轨会话隔离（独立 session_id），B 轨确认办理不影响 A 轨 ·
          演示语料为虚构「钱塘大学」合成数据
        </footer>
      </div>
    </main>
  );
}
