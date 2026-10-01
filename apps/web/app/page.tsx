"use client";

import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { Info, Loader2, PanelLeft, Send, Sparkles, TriangleAlert, X } from "lucide-react";
import {
  API_BASE,
  createSession,
  deleteSession,
  fetchHealth,
  fetchMessages,
  listSessions,
  renameSession,
  streamChat,
  type ActionResult,
  type ChatEvent,
  type ChatMode,
  type Citation,
  type DoneReason,
  type HealthInfo,
  type HistoryMessage,
  type PendingAction,
  type SessionInfo,
  type Step,
} from "@/lib/api";
import { useRequireUser } from "@/lib/auth";
import { ROLE_LABEL } from "@/lib/labels";
import Answer from "@/components/answer";
import BlurText from "@/components/BlurText";
import SessionList from "@/components/session-list";
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

/** 历史恢复（P22）：对话级纯文本 → 现有消息结构（静态态，不渲染事件级卡片）。 */
function historyToMsg(m: HistoryMessage): Msg {
  return { role: m.role, text: m.text, steps: [], citations: [], done: true };
}

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

const LS_CURRENT_SESSION = "gewu.current-session";

export default function Home() {
  const { user } = useRequireUser(); // P21：登录页守卫；role 服务端权威（只读展示）
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<ChatMode>("auto");
  const [sending, setSending] = useState(false);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  // P22 会话（服务端资源）：列表 + 当前 id（state 驱动侧栏高亮，ref 供 send 闭包直读）
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [currentSession, setCurrentSession] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sessionErr, setSessionErr] = useState<string | null>(null);
  const sessionId = useRef<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchHealth().then(setHealth);
  }, []);

  // 加载：拉列表 → localStorage 读 current（列表校验，无效丢弃重建）→ 历史恢复
  useEffect(() => {
    if (!user) return;
    let alive = true;
    (async () => {
      try {
        const list = await listSessions("chat");
        if (!alive) return;
        setSessions(list);
        const saved = typeof localStorage !== "undefined" ? localStorage.getItem(LS_CURRENT_SESSION) : null;
        let target = saved ? list.find((s) => s.session_id === saved) : undefined;
        if (!target) {
          target = await createSession("chat");
          if (!alive) return;
          setSessions((prev) => [target as SessionInfo, ...prev]);
        }
        setCurrentSession(target.session_id);
        sessionId.current = target.session_id;
        localStorage.setItem(LS_CURRENT_SESSION, target.session_id);
        const history = await fetchMessages(target.session_id).catch(() => []);
        if (alive && history.length > 0) setMessages(history.map(historyToMsg));
      } catch (err) {
        if (alive) setSessionErr(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      alive = false;
    };
  }, [user]);

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

  /** 切换会话：拉历史恢复渲染（对话级静态态）。 */
  async function switchTo(id: string) {
    setDrawerOpen(false);
    if (id === currentSession) return;
    setCurrentSession(id);
    sessionId.current = id;
    localStorage.setItem(LS_CURRENT_SESSION, id);
    setMessages([]);
    try {
      const history = await fetchMessages(id);
      setMessages(history.map(historyToMsg));
    } catch (err) {
      setSessionErr(err instanceof Error ? err.message : String(err));
    }
  }

  async function newChat() {
    setDrawerOpen(false);
    setSessionErr(null);
    try {
      const s = await createSession("chat");
      setSessions((prev) => [s, ...prev]);
      setCurrentSession(s.session_id);
      sessionId.current = s.session_id;
      localStorage.setItem(LS_CURRENT_SESSION, s.session_id);
      setMessages([]);
    } catch (err) {
      setSessionErr(err instanceof Error ? err.message : String(err));
    }
  }

  async function renameS(id: string, title: string) {
    try {
      const updated = await renameSession(id, title);
      setSessions((prev) => prev.map((s) => (s.session_id === id ? updated : s)));
    } catch (err) {
      setSessionErr(err instanceof Error ? err.message : String(err));
    }
  }

  async function removeSession(id: string) {
    try {
      await deleteSession(id);
      const rest = sessions.filter((s) => s.session_id !== id);
      setSessions(rest);
      if (id === currentSession) {
        if (rest.length > 0) await switchTo(rest[0].session_id);
        else await newChat();
      }
    } catch (err) {
      setSessionErr(err instanceof Error ? err.message : String(err));
    }
  }

  async function send(text?: string) {
    const question = (text ?? input).trim();
    if (!question || sending || !sessionId.current) return;
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
        { sessionId: sessionId.current },
      );
    } catch (err) {
      patchLast({ error: err instanceof Error ? err.message : String(err), done: true });
    } finally {
      setSending(false);
      // 服务端副作用（title 首问回填 + updated_at 排序）落库后刷侧栏
      listSessions("chat").then(setSessions).catch(() => {});
    }
  }

  const sessionList = (
    <SessionList
      sessions={sessions}
      currentId={currentSession}
      onSelect={(id) => void switchTo(id)}
      onNew={() => void newChat()}
      onRename={(id, title) => renameS(id, title)}
      onDelete={(id) => removeSession(id)}
    />
  );

  return (
    <main className="relative flex h-full flex-col">
      {/* 环境光：画布顶部的暖色氛围（陶土/琥珀光斑 + 微点阵），编辑式纸感的呼吸（DESIGN.md empty-state） */}
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-96 overflow-hidden">
        <div className="absolute -top-24 left-1/4 size-96 rounded-full bg-primary/[0.07] blur-3xl dark:bg-primary/10" />
        <div className="absolute -top-10 right-1/4 size-72 rounded-full bg-amber-400/[0.07] blur-3xl dark:bg-amber-400/10" />
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_1px_1px,var(--color-foreground)_1px,transparent_0)] bg-[size:22px_22px] opacity-[0.05] [mask-image:radial-gradient(70%_60%_at_50%_0%,black,transparent)] dark:opacity-[0.07]" />
      </div>

      {/* P22 双栏：桌面左栏 260px 常驻侧栏（导航 chrome，hairline 分隔） */}
      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-65 shrink-0 flex-col border-r md:flex">{sessionList}</aside>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
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
          {sessionErr && (
            <div className="mx-auto w-full max-w-3xl space-y-2 px-4 pt-3">
              <Alert variant="destructive" className="py-2.5">
                <TriangleAlert className="size-4" aria-hidden />
                <AlertDescription>
                  会话操作失败：{sessionErr}
                  <button className="ml-2 underline underline-offset-2" onClick={() => setSessionErr(null)}>
                    关闭
                  </button>
                </AlertDescription>
              </Alert>
            </div>
          )}

          <section
            aria-label="对话区"
            aria-live="polite"
            aria-atomic="false"
            className="min-h-0 flex-1 overflow-y-auto"
          >
            {/* 移动端会话入口：sticky 窄条（桌面侧栏隐藏时才出现） */}
            <div className="sticky top-0 z-10 bg-background/80 px-4 py-2 backdrop-blur md:hidden">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setDrawerOpen(true)}
                className="gap-1.5"
              >
                <PanelLeft className="size-4" aria-hidden />
                会话
              </Button>
            </div>
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
                    disabled={sending || !currentSession}
                    className="rounded-full border bg-card px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
                  >
                    {s}
                  </button>
                ))}
              </div>
              {/* composer：hairline 卡 + 主色 focus 环，实心主色发送钮（DESIGN.md composer）。
                  身份为只读徽章——P21 起 role 服务端权威（users.role），不可在客户端切换 */}
              <div className="flex items-end gap-2 rounded-2xl border bg-card p-2 shadow-sm transition-colors focus-within:border-primary/50 focus-within:ring-4 focus-within:ring-primary/10">
                {user && (
                  <span
                    className="h-9 shrink-0 rounded-lg bg-primary/10 px-3 text-xs font-medium leading-9 text-primary"
                    title={`${user.display_name || user.email} · 权限随账号角色`}
                  >
                    {ROLE_LABEL[user.role] ?? user.role}身份
                  </span>
                )}
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
                  placeholder={
                    currentSession ? "输入你的问题，Enter 发送，Shift+Enter 换行" : "正在准备会话…"
                  }
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
                  <Button
                    onClick={() => send()}
                    disabled={sending || !input.trim() || !currentSession}
                    className="h-9"
                  >
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
        </div>
      </div>

      {/* 移动端抽屉（自写零依赖）：fixed + backdrop，桌面侧栏常驻时不可达 */}
      {drawerOpen && (
        <>
          <div
            className="fixed inset-0 z-40 bg-foreground/20 md:hidden"
            onClick={() => setDrawerOpen(false)}
            aria-hidden
          />
          <aside
            className="fixed inset-y-0 left-0 z-50 flex w-72 flex-col border-r bg-background md:hidden"
            role="dialog"
            aria-label="会话列表"
          >
            <div className="flex h-13 shrink-0 items-center justify-between border-b px-3">
              <span className="text-sm font-semibold">历史会话</span>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setDrawerOpen(false)}
                aria-label="关闭会话列表"
              >
                <X className="size-4" aria-hidden />
              </Button>
            </div>
            {sessionList}
          </aside>
        </>
      )}
    </main>
  );
}
