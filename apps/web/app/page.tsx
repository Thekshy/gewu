"use client";

import { useEffect, useRef, useState } from "react";
import { useTheme } from "next-themes";
import { motion } from "motion/react";
import { Info, Loader2, Send, Sparkles, TriangleAlert } from "lucide-react";
import {
  API_BASE,
  fetchHealth,
  streamChat,
  type ActionResult,
  type ChatEvent,
  type ChatMode,
  type Citation,
  type DoneReason,
  type HealthInfo,
  type PendingAction,
  type Role,
  type Step,
} from "@/lib/api";
import Answer from "@/components/answer";
import Aurora from "@/components/Aurora";
import BlurText from "@/components/BlurText";
import ClickSpark from "@/components/ClickSpark";
import {
  CitationsRow,
  ConfirmCard,
  DoneMeta,
  ReceiptAlert,
  ResearchTrace,
  RouteBadge,
  SlotCard,
  StatusLine,
} from "@/components/message-parts";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

interface Msg {
  role: "user" | "assistant";
  text: string;
  route?: string;
  reason?: string;
  status?: string;
  steps: Step[];
  citations: Citation[];
  slotQ?: { slot: string; question: string };
  pendingAction?: PendingAction;
  actionResult?: ActionResult;
  latency?: number;
  doneReason?: DoneReason;
  error?: string;
  done: boolean;
}

const ROLE_OPTIONS: { value: Role; label: string }[] = [
  { value: "student", label: "学生身份" },
  { value: "counselor", label: "辅导员身份" },
];
const MODE_OPTIONS: { value: ChatMode; label: string }[] = [
  { value: "auto", label: "自动路由" },
  { value: "direct", label: "强制直答" },
  { value: "research", label: "强制研究" },
  { value: "react", label: "ReAct 自主编排" },
];

const SUGGESTIONS = [
  "帮我预约明天晚上的羽毛球馆打班级比赛",
  "帮我请下周一到下周二的事假，另外超过 7 天是不是要教务处批？",
  "转专业之后原课程绩点还算吗？会影响保研吗？",
];

export default function Home() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<ChatMode>("auto");
  const [role, setRole] = useState<Role>("student");
  const [sending, setSending] = useState(false);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const { resolvedTheme } = useTheme();
  const bottomRef = useRef<HTMLDivElement>(null);
  const sessionId = useRef<string>(
    typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `s-${Date.now()}`,
  );

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
      await streamChat(
        question,
        mode,
        (ev: ChatEvent) => {
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
            case "slot_question":
              patchLast({
                slotQ: {
                  slot: String(ev.slot ?? ""),
                  question: String(ev.question ?? ""),
                },
              });
              break;
            case "pending_action":
              patchLast({
                pendingAction: {
                  tool: String(ev.tool),
                  label: String(ev.label),
                  args: (ev.args as Record<string, string>) ?? {},
                },
              });
              break;
            case "action_result":
              patchLast({
                actionResult: {
                  tool: String(ev.tool),
                  success: Boolean(ev.success),
                  message: String(ev.message ?? ""),
                  receipt: (ev.receipt as string | null) ?? null,
                },
              });
              break;
            case "error":
              patchLast({ error: String(ev.message ?? "未知错误") });
              break;
            case "done":
              patchLast({
                done: true,
                latency: Number(ev.latency_ms ?? 0),
                status: undefined,
                doneReason: (ev.reason as DoneReason) || "completed",
              });
              break;
          }
        },
        { sessionId: sessionId.current, role },
      );
    } catch (err) {
      patchLast({ error: err instanceof Error ? err.message : String(err), done: true });
    } finally {
      setSending(false);
    }
  }

  return (
    <main className="flex h-full flex-col">
      {health && !health.llm && (
        <div className="mx-auto w-full max-w-3xl space-y-2 px-4 pt-3">
          <Alert className="py-2.5">
            <Info className="size-4" aria-hidden />
            <AlertDescription>
              检索演示模式：未配置 LLM_API_KEY。知识问答展示检索节选；业务办理走完整流程（槽位收集 → 确认 → 回执）。
            </AlertDescription>
          </Alert>
        </div>
      )}
      {!health && (
        <div className="mx-auto w-full max-w-3xl space-y-2 px-4 pt-3">
          <Alert variant="destructive" className="py-2.5">
            <TriangleAlert className="size-4" aria-hidden />
            <AlertDescription>连不上后端（{API_BASE}），请先启动 API 服务。</AlertDescription>
          </Alert>
        </div>
      )}

      <section aria-label="对话区" className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-4 px-4 py-6">
          {messages.length === 0 && (
            <div className="relative flex min-h-[65vh] flex-col items-center justify-center gap-3 text-center">
              <div className="absolute inset-0 -z-10 opacity-70 [mask-image:radial-gradient(ellipse_65%_55%_at_50%_45%,black_25%,transparent_78%)]">
                <Aurora
                  colorStops={["#22d3ee", "#0e7490", "#818cf8"]}
                  amplitude={0.8}
                  blend={0.6}
                  speed={0.45}
                  lightMode={resolvedTheme !== "dark"}
                />
              </div>
              <motion.div
                initial={{ opacity: 0, scale: 0.85 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.45, ease: "easeOut" }}
                className="flex size-14 items-center justify-center rounded-2xl bg-gradient-to-br from-cyan-600 to-teal-700 text-2xl font-semibold text-white shadow-md"
                aria-hidden
              >
                格
              </motion.div>
              <BlurText
                text="问校园政策，或者直接办事——预约场馆、提交请假。"
                animateBy="letters"
                delay={55}
                stepDuration={0.3}
                className="justify-center text-lg font-medium leading-relaxed"
              />
              <motion.p
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 1.2, duration: 0.5 }}
                className="max-w-md text-sm text-muted-foreground"
              >
                办理类请求会经过：槽位收集 → 确认摘要 → 执行 → 回执；写操作必须确认后才会执行。
              </motion.p>
            </div>
          )}

          {messages.map((msg, i) =>
            msg.role === "user" ? (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.25, ease: "easeOut" }}
                className="flex justify-end"
              >
                <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm leading-relaxed text-primary-foreground">
                  {msg.text}
                </div>
              </motion.div>
            ) : (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.25, ease: "easeOut" }}
                className="flex items-start gap-2.5"
              >
                <div
                  className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
                  aria-hidden
                >
                  <Sparkles className="size-4" />
                </div>
                <div className="min-w-0 flex-1 space-y-2.5 rounded-2xl rounded-tl-sm border bg-card px-4 py-3 shadow-xs">
                  <div className="flex flex-wrap items-center gap-2">
                    {msg.route && <RouteBadge route={msg.route} reason={msg.reason} />}
                  </div>
                  <ResearchTrace steps={msg.steps} defaultOpen={!msg.done} />
                  {msg.status && <StatusLine text={msg.status} />}
                  {msg.slotQ && <SlotCard slot={msg.slotQ.slot} />}
                  {msg.text && <Answer text={msg.text} />}
                  {!msg.text && !msg.status && msg.steps.length === 0 && !msg.done && (
                    <StatusLine text="思考中…" />
                  )}

                  {msg.pendingAction && !msg.actionResult && msg.done && (
                    <ConfirmCard
                      pending={msg.pendingAction}
                      onConfirm={() => send("确认")}
                      onCancel={() => send("取消")}
                      disabled={sending}
                    />
                  )}

                  {msg.actionResult && <ReceiptAlert result={msg.actionResult} />}

                  <CitationsRow citations={msg.citations} withSource />
                  {msg.error && (
                    <Alert variant="destructive" className="py-2.5">
                      <TriangleAlert className="size-4" aria-hidden />
                      <AlertDescription>出错了：{msg.error}</AlertDescription>
                    </Alert>
                  )}
                  {msg.done && (
                    <DoneMeta latency={msg.latency} doneReason={msg.doneReason} />
                  )}
                </div>
              </motion.div>
            ),
          )}
          <div ref={bottomRef} />
        </div>
      </section>

      <div className="shrink-0 border-t bg-background">
        <div className="mx-auto w-full max-w-3xl space-y-2 px-4 py-3">
          <div className="flex flex-wrap gap-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => send(s)}
                disabled={sending}
                className="rounded-full border bg-card px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
              >
                {s}
              </button>
            ))}
          </div>
          <div className="flex items-end gap-2 rounded-2xl border bg-card p-2 shadow-xs focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/20">
            <Select value={role} onValueChange={(v) => setRole(v as Role)}>
              <SelectTrigger className="h-9 w-27 shrink-0" aria-label="演示身份（权限不同）">
                <SelectValue>{ROLE_OPTIONS.find((o) => o.value === role)?.label}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {ROLE_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={mode} onValueChange={(v) => setMode(v as ChatMode)}>
              <SelectTrigger className="h-9 w-31 shrink-0" aria-label="回答模式">
                <SelectValue>{MODE_OPTIONS.find((o) => o.value === mode)?.label}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {MODE_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <textarea
              value={input}
              placeholder="输入你的问题，Enter 发送，Shift+Enter 换行"
              rows={1}
              className="max-h-40 min-h-9 flex-1 resize-none border-0 bg-transparent px-1 py-2 text-sm outline-none placeholder:text-muted-foreground"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  send();
                }
              }}
            />
            <div className="shrink-0">
              <ClickSpark sparkColor="#ffffff" sparkCount={8} duration={500}>
                <Button onClick={() => send()} disabled={sending || !input.trim()} className="h-9">
                  {sending ? <Loader2 className="animate-spin" aria-hidden /> : <Send aria-hidden />}
                  {sending ? "处理中…" : "发送"}
                </Button>
              </ClickSpark>
            </div>
          </div>
        </div>
      </div>

      <footer className="shrink-0 px-4 pb-2 text-center text-[11px] text-muted-foreground">
        {health ? `${health.docs} 篇文档 / ${health.chunks} chunks · ` : ""}
        演示语料与业务系统均为虚构的「钱塘大学」合成数据 · 格物 Gewu 是开源的个人求职展示项目
      </footer>
    </main>
  );
}
