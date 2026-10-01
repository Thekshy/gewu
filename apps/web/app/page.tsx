"use client";

import { useEffect, useRef, useState } from "react";
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
import BlurText from "@/components/BlurText";
import {
  CitationsRow,
  ConfirmCard,
  DoneMeta,
  ReceiptAlert,
  ResearchTrace,
  RouteBadge,
  SlotCard,
  StatusLine,
  TypingDots,
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
    <main className="relative flex h-full flex-col">
      {/* 环境光：画布顶部的暖色氛围（陶土/琥珀光斑 + 微点阵），编辑式纸感的呼吸（DESIGN.md empty-state） */}
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-96 overflow-hidden">
        <div className="absolute -top-24 left-1/4 size-96 rounded-full bg-primary/[0.07] blur-3xl dark:bg-primary/10" />
        <div className="absolute -top-10 right-1/4 size-72 rounded-full bg-amber-400/[0.07] blur-3xl dark:bg-amber-400/10" />
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_1px_1px,var(--color-foreground)_1px,transparent_0)] bg-[size:22px_22px] opacity-[0.05] [mask-image:radial-gradient(70%_60%_at_50%_0%,black,transparent)] dark:opacity-[0.07]" />
      </div>
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

      <section aria-label="对话区" aria-live="polite" aria-atomic="false" className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-4 px-4 py-6">
          {messages.length === 0 && (
            <div className="relative flex min-h-[65vh] flex-col items-center justify-center gap-4 text-center">
              {/* 空态：编辑式排版——衬线大标语直接铺在画布上，无卡片（DESIGN.md empty-state） */}
              <motion.div
                initial={{ opacity: 0, scale: 0.85 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.45, ease: "easeOut" }}
                className="flex size-16 items-center justify-center rounded-xl bg-primary font-display text-3xl font-semibold text-primary-foreground shadow-md"
                aria-hidden
              >
                格
              </motion.div>
              <BlurText
                text="问校园政策，或者直接办事——预约场馆、提交请假。"
                animateBy="letters"
                delay={55}
                stepDuration={0.3}
                className="justify-center font-display text-2xl font-semibold leading-snug sm:text-3xl"
              />
              {/* 说明行不做延迟入场（operate.md：产品页不排加载序列，
                  1.2s delay 会被检测器采样成低对比）；入场时刻只留 BlurText */}
              <p className="max-w-md text-sm text-muted-foreground">
                办理类请求会经过：槽位收集 → 确认摘要 → 执行 → 回执；写操作必须确认后才会执行。
              </p>
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
                {/* 用户消息：安静色块，无渐变无描边（DESIGN.md user-message） */}
                <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-secondary px-4 py-2.5 text-sm leading-relaxed text-secondary-foreground">
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
                  className="mt-1 flex size-7 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary"
                  aria-hidden
                >
                  <Sparkles className="size-4" />
                </div>
                {/* assistant 回答无气泡无卡片，直接铺在画布上（DESIGN.md assistant-message）；
                    层级交给 RouteBadge/trace/引用行与 ConfirmCard 唯一高亮块 */}
                <div className="min-w-0 flex-1 space-y-2.5 py-0.5">
                  <div className="flex flex-wrap items-center gap-2">
                    {msg.route && <RouteBadge route={msg.route} reason={msg.reason} />}
                  </div>
                  <ResearchTrace steps={msg.steps} defaultOpen={!msg.done} />
                  {msg.status && <StatusLine text={msg.status} />}
                  {msg.slotQ && <SlotCard slot={msg.slotQ.slot} />}
                  {msg.text && <Answer text={msg.text} streaming={!msg.done} />}
                  {!msg.text && !msg.status && msg.steps.length === 0 && !msg.done && (
                    <div role="status" className="space-y-3">
                      <TypingDots />
                      {/* 首包前骨架：模拟文字节奏的 shimmer 占位行 */}
                      <div className="max-w-md space-y-2 pt-1" aria-hidden>
                        <div className="shimmer-line h-3 w-full rounded-md" />
                        <div className="shimmer-line h-3 w-10/12 rounded-md" />
                        <div className="shimmer-line h-3 w-7/12 rounded-md" />
                      </div>
                    </div>
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

      {/* 底部操作带：hairline 分隔即可。bg-background 是画布同色的冗余，
          且 border-t + bg 组合会被 impeccable 判成 card-like 嵌套（P20 基线） */}
      <div className="shrink-0 border-t">
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
          {/* composer：hairline 卡 + 主色 focus 环，实心主色发送钮（DESIGN.md composer） */}
          <div className="flex items-end gap-2 rounded-2xl border bg-card p-2 shadow-sm transition-colors focus-within:border-primary/50 focus-within:ring-4 focus-within:ring-primary/10">
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
              <Button onClick={() => send()} disabled={sending || !input.trim()} className="h-9">
                {sending ? <Loader2 className="animate-spin" aria-hidden /> : <Send aria-hidden />}
                {sending ? "处理中…" : "发送"}
              </Button>
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
