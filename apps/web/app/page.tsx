"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import {
  CalendarCheck,
  ChevronRight,
  FilePenLine,
  GraduationCap,
  Info,
  Loader2,
  PanelLeft,
  Send,
  TriangleAlert,
  X,
} from "lucide-react";
import {
  API_BASE,
  createSession,
  deleteSession,
  fetchHealth,
  fetchMessages,
  listSessions,
  renameSession,
  sendFeedback,
  streamChat,
  type ActionResult,
  type ChatEvent,
  type Citation,
  type DoneReason,
  type FeedbackRating,
  type HealthInfo,
  type HistoryMessage,
  type PendingAction,
  type SessionInfo,
  type Step,
} from "@/lib/api";
import { useRequireUser } from "@/lib/auth";
import { ROLE_LABEL } from "@/lib/labels";
import { cn } from "@/lib/utils";
import { NavColumn } from "@/components/nav";
import ThemeToggle from "@/components/theme-toggle";
import GithubLink from "@/components/github-link";
import UserMenu from "@/components/user-menu";
import Answer from "@/components/answer";
import SessionList from "@/components/session-list";
import {
  CitationsRow,
  ConfirmCard,
  DoneMeta,
  MessageActions,
  ReceiptAlert,
  ResearchTrace,
  RouteBadge,
  SlotCard,
  StatusLine,
  TypingDots,
} from "@/components/message-parts";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

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
  /** P25：done 之后 SSE 追发的建议追问（晚到渐进渲染）；点击后本组置灰。 */
  followUps?: string[];
  followUpSent?: boolean;
  feedback?: FeedbackRating;
}

/** 历史恢复（P22）：对话级纯文本 → 现有消息结构（静态态，不渲染事件级卡片）。 */
function historyToMsg(m: HistoryMessage): Msg {
  return { role: m.role, text: m.text, steps: [], citations: [], done: true };
}

/** P34-2 空态任务预览卡（america.gov task preview 同构）：用真实任务形态代替
 *  纯文字 chip——「展示你能办成什么」；编辑式白名单第三形态（DESIGN.md task-card）。 */
const TASK_CARDS = [
  {
    icon: CalendarCheck,
    title: "预约场馆",
    desc: "羽毛球馆 · 明晚 · 班级赛",
    q: "帮我预约明天晚上的羽毛球馆打班级比赛",
  },
  {
    icon: FilePenLine,
    title: "提交请假",
    desc: "事假 1 天 · 附政策依据",
    q: "帮我请下周一到下周二的事假，另外超过 7 天是不是要教务处批？",
  },
  {
    icon: GraduationCap,
    title: "查制度",
    desc: "转专业绩点 · 保研影响",
    q: "转专业之后原课程绩点还算吗？会影响保研吗？",
  },
];

/** P34-2 输入框轮换示例问题（america.gov rotating questions 同构）：
 *  仅输入为空时轮换，开始打字即停。 */
const ROTATING_EXAMPLES = [
  "预约体育馆要提前几天？",
  "请假超过 7 天谁来审批？",
  "转专业后原课绩点怎么算？",
  "奖学金评定的时间线是？",
  "校外人员能进图书馆吗？",
];

const LS_CURRENT_SESSION = "gewu.current-session";

export default function Home() {
  const { user } = useRequireUser(); // P21：登录页守卫；role 服务端权威（只读展示）
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  // P22 会话（服务端资源）：列表 + 当前 id（state 驱动侧栏高亮，ref 供 send 闭包直读）
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [currentSession, setCurrentSession] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sessionErr, setSessionErr] = useState<string | null>(null);
  const sessionId = useRef<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  // P34-2 轮换示例问题：输入为空时 2.8s 轮换，有值即停
  const [exampleIdx, setExampleIdx] = useState(0);
  useEffect(() => {
    if (input) return;
    const t = setInterval(() => setExampleIdx((v) => (v + 1) % ROTATING_EXAMPLES.length), 2800);
    return () => clearInterval(t);
  }, [input]);

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

  function patchAt(i: number, patch: Partial<Msg>) {
    setMessages((prev) => {
      if (i < 0 || i >= prev.length) return prev;
      const next = [...prev];
      next[i] = { ...next[i], ...patch };
      return next;
    });
  }

  /** P25：👍/👎 落库（乐观置灰，失败回弹）；轮次锚 = 该回答上方最近的用户问题。 */
  function rate(i: number, rating: FeedbackRating) {
    const target = messages[i];
    if (!target || target.feedback) return;
    let question = "";
    for (let j = i - 1; j >= 0; j--) {
      if (messages[j].role === "user") {
        question = messages[j].text;
        break;
      }
    }
    if (!question || !sessionId.current) return;
    patchAt(i, { feedback: rating });
    sendFeedback(sessionId.current, question, rating).catch((err) => {
      console.warn("反馈上报失败（已回弹）：", err);
      patchAt(i, { feedback: undefined });
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
        "auto", // P31-2：mode 选择 UI 退役，主路固定 auto（react=auto 语义）
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
            case "answer_reset":
              // P30：流式中间轮撤回——已显示文本转存为一条 step（思考轨迹行）
              patchLast((m) =>
                m.text
                  ? {
                      ...m,
                      steps: [
                        ...m.steps,
                        {
                          type: "step",
                          index: m.steps.length + 1,
                          subquestion: m.text,
                          sources: [],
                        } as unknown as Step,
                      ],
                      text: "",
                    }
                  : m,
              );
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
            case "follow_ups":
              // P25：done 之后晚到（flash 生成 ≤8s），渐进渲染不阻塞输入
              patchLast({ followUps: (ev.items as string[]) ?? [] });
              break;
            case "done":
              patchLast({
                done: true,
                latency: Number(ev.latency_ms ?? 0),
                status: undefined,
                doneReason: (ev.reason as DoneReason) || "completed",
              });
              // done 即解锁输入（follow_ups 可能晚到；aborted/error 走 finally 兜底）
              setSending(false);
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
      {/* skip 链接（P25 a11y，america.gov 三连 skip 的从简两枚）：focus 时可见 */}
      <a
        href="#latest-msg"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-1.5 focus:text-sm focus:shadow-md"
      >
        跳到最新回答
      </a>
      <a
        href="#composer-input"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-1.5 focus:text-sm focus:shadow-md"
      >
        跳到输入框
      </a>
      {/* P37：原「环境光雾 + 微点阵」退役——在墨白/印刷方向里它读起来是
          一团灰脏点，而不是光（P34 视觉评审也曾判它偏强）。结构感改由发丝线
          与留白层级承担，不靠模糊光斑。 */}

      {/* P35 app-shell：chat 页 chrome-less——品牌/视图导航/登录态全部并入左侧栏
          （Claude.ai/ChatGPT 形态），内容区顶天立地；工具页顶栏由 AppHeader 条件渲染 */}
      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-65 shrink-0 flex-col bg-sidebar md:flex">
          <div className="flex h-13 shrink-0 items-center gap-2.5 px-4">
            <Link href="/" className="flex items-center gap-2.5">
              <span
                className="flex size-7 items-center justify-center rounded-md bg-primary font-display text-sm font-semibold text-primary-foreground shadow-sm"
                aria-hidden
              >
                格
              </span>
              <span className="font-display text-[15px] font-semibold">格物</span>
              <span className="hidden text-xs text-muted-foreground lg:inline">校园智能问答</span>
            </Link>
          </div>
          <div className="px-2 pt-1">
            <NavColumn />
          </div>
          <div className="min-h-0 flex-1">{sessionList}</div>
          <div className="flex shrink-0 items-center justify-between gap-2 border-t px-3 py-2">
            <UserMenu />
            <div className="flex items-center gap-0.5">
              <GithubLink />
              <ThemeToggle />
            </div>
          </div>
        </aside>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {user?.role === "guest" && (
            <div className="mx-auto w-full max-w-3xl space-y-2 px-4 pt-3">
              <Alert className="py-2.5">
                <Info className="size-4" aria-hidden />
                <AlertDescription>
                  游客模式：免登录直接体验，数据短期保留；部分功能（长期记忆、控制台）需
                  <Link href="/login" className="mx-1 underline underline-offset-2">
                    登录
                  </Link>
                  后使用。
                </AlertDescription>
              </Alert>
            </div>
          )}
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
            {/* 移动端顶条（P35 app-shell）：品牌 + 菜单入口，桌面侧栏隐藏时才出现 */}
            <div className="sticky top-0 z-10 flex items-center justify-between bg-background/80 px-4 py-2 backdrop-blur md:hidden">
              <Link href="/" className="flex items-center gap-2">
                <span
                  className="flex size-6 items-center justify-center rounded-md bg-primary font-display text-xs font-semibold text-primary-foreground"
                  aria-hidden
                >
                  格
                </span>
                <span className="font-display text-sm font-semibold">格物</span>
              </Link>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="打开菜单"
                onClick={() => setDrawerOpen(true)}
              >
                <PanelLeft className="size-4" aria-hidden />
              </Button>
            </div>
            <div
              className={cn(
                "mx-auto w-full px-4 pb-6 pt-10",
                messages.length === 0
                  ? "max-w-5xl"
                  : "flex min-h-full max-w-3xl flex-col justify-end space-y-5",
              )}
            >
              {messages.length === 0 && (
                <div className="grid min-h-[58vh] content-center gap-10 lg:grid-cols-12 lg:gap-12">
                  {/* P37 空态重构：原来是「居中方印 + 标语 + 两行小字」悬浮在大片死白里
                      （死白无结构 = 廉价感第一来源）。改为左栏主张、右栏可执行任务的
                      两栏工作台：左对齐、有层级、右侧是真实任务形态而非装饰。 */}
                  <div className="lg:col-span-5">
                    <div className="flex items-center gap-3">
                      <span
                        className="flex size-9 items-center justify-center rounded-md bg-primary font-display text-base font-semibold text-primary-foreground"
                        aria-hidden
                      >
                        格
                      </span>
                      <span className="t-meta text-muted-foreground">
                        <span className="t-num">{health?.docs ?? "—"}</span> 篇制度文档 ·{" "}
                        <span className="t-num">{health?.chunks ?? "—"}</span> 段索引
                      </span>
                    </div>
                    <h1 className="t-display mt-7 font-display">
                      问校园政策，
                      <br />
                      或者直接办事。
                    </h1>
                    <p className="t-lead mt-5 max-w-sm text-muted-foreground">
                      办理类请求会经过槽位收集 → 确认摘要 → 执行 → 回执，写操作必须确认后才会执行。
                    </p>
                    <p className="t-small mt-3 max-w-sm text-muted-foreground/75">
                      回答仅基于钱塘大学官方制度文档生成，全部引用可溯源到发文部门。
                    </p>
                  </div>
                  {/* 右栏用发丝线分行，不用三张同形圆角卡——「同形卡 + 统一阴影 + 渐变洗」
                      是 DESIGN.md 负参照里点名的模板感来源 */}
                  <div className="lg:col-span-7">
                    <p className="t-meta rule-b flex items-center gap-2 pb-2 font-semibold text-muted-foreground">
                      <span className="size-2 bg-seal" aria-hidden />
                      可以直接办的事
                    </p>
                    <ul className="divide-y divide-border/70">
                      {TASK_CARDS.map((t) => (
                        <li key={t.title}>
                          <button
                            type="button"
                            onClick={() => send(t.q)}
                            disabled={sending || !currentSession}
                            className="group relative flex w-full items-center gap-4 py-4 text-left disabled:pointer-events-none disabled:opacity-50"
                          >
                            {/* P37「落笔」：hover 时发丝线从左画出，给出「可点」的书写感 */}
                            <span
                              aria-hidden
                              className="pointer-events-none absolute inset-x-0 bottom-0 h-px origin-left scale-x-0 bg-seal/40 transition-transform duration-200 ease-out-expo group-hover:scale-x-100"
                            />
                            <t.icon
                              className="size-4 shrink-0 text-muted-foreground transition-colors group-hover:text-seal"
                              aria-hidden
                            />
                            <span className="min-w-0 flex-1">
                              <span className="t-h3 block">{t.title}</span>
                              <span className="t-small block text-muted-foreground">{t.desc}</span>
                            </span>
                            <ChevronRight
                              className="size-4 shrink-0 text-muted-foreground/50 transition-transform duration-150 ease-out-expo group-hover:translate-x-0.5 group-hover:text-foreground"
                              aria-hidden
                            />
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}
              {messages.map((msg, i) =>
                msg.role === "user" ? (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
                    layout="position"
                    className="flex justify-end"
                  >
                    {/* 用户消息：primary 实底气泡（P25 起，america.gov 同款；DESIGN.md user-message） */}
                    <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm leading-relaxed text-primary-foreground">
                      {msg.text}
                    </div>
                  </motion.div>
                ) : (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
                    layout="position"
                    className="flex items-start gap-2.5"
                  >
{/* 页边标记（P37）：生成中呼吸、完成时落定——替代转圈，
                        也给「正在思考」一点生命感 */}
                    <div className="mt-2 flex w-2 shrink-0 justify-center" aria-hidden>
                      <motion.span
                        className="size-1.5 rounded-full bg-seal"
                        animate={
                          msg.done
                            ? { scale: 1, opacity: 1 }
                            : { scale: [1, 1.45, 1], opacity: [0.55, 1, 0.55] }
                        }
                        transition={
                          msg.done
                            ? { duration: 0.2 }
                            : { duration: 1.4, repeat: Infinity, ease: "easeInOut" }
                        }
                      />
                    </div>
                    {/* assistant 回答无气泡无卡片，直接铺在画布上（DESIGN.md assistant-message）；
                        层级交给 RouteBadge/trace/引用行与 ConfirmCard 唯一高亮块 */}
                    <div className="min-w-0 flex-1 space-y-2.5 py-0.5">
                      <div className="flex flex-wrap items-center gap-2">
                        {msg.route && <RouteBadge route={msg.route} reason={msg.reason} />}
                      </div>
                      <ResearchTrace steps={msg.steps} live={!msg.done} />
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
                          <AlertDescription>
                            出错了：{msg.error}
                            {messages[i - 1]?.role === "user" && (
                              <button
                                className="ml-2 underline underline-offset-2 disabled:pointer-events-none disabled:opacity-50"
                                onClick={() => send(messages[i - 1].text)}
                                disabled={sending}
                              >
                                重试
                              </button>
                            )}
                          </AlertDescription>
                        </Alert>
                      )}
                      {msg.done && !msg.error && (
                        <motion.div
                          initial={i === 1 ? { opacity: 0, y: 8 } : false}
                          animate={{ opacity: 1, y: 0 }}
                          transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1], delay: 0.1 }}
                        >
                          {/* 首答完成时刻（编辑式白名单之二）：操作条收束浮现，
                              一次性节奏——后续回答不再 stagger（DESIGN.md message-actions） */}
                          <MessageActions
                            citations={msg.citations}
                            text={msg.text}
                            feedback={msg.feedback}
                            onFeedback={(r) => rate(i, r)}
                            onlyCopy={msg.route === undefined && msg.latency === undefined}
                          />
                        </motion.div>
                      )}
                      {msg.done && !msg.error && msg.followUps && msg.followUps.length > 0 && (
                        <div className="flex flex-wrap gap-2 pt-1">
                          {msg.followUps.map((q, qi) => (
                            <motion.button
                              key={q}
                              initial={{ opacity: 0, y: 6 }}
                              animate={{ opacity: 1, y: 0 }}
                              transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1], delay: i === 1 ? qi * 0.06 : 0 }}
                              onClick={() => {
                                patchAt(i, { followUpSent: true });
                                send(q);
                              }}
                              disabled={sending || msg.followUpSent}
                              className="inline-flex items-center gap-1 rounded-full border bg-card px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
                            >
                              <ChevronRight className="size-3" aria-hidden />
                              {q}
                            </motion.button>
                          ))}
                        </div>
                      )}
                      {msg.done && (
                        <DoneMeta latency={msg.latency} doneReason={msg.doneReason} />
                      )}
                    </div>
                  </motion.div>
                ),
              )}
              <div id="latest-msg" ref={bottomRef} />
            </div>
          </section>

          {/* 底部操作带：hairline 分隔即可。bg-background 是画布同色的冗余，
              且 border-t + bg 组合会被 impeccable 判成 card-like 嵌套（P20 基线） */}
          <div className="rule-t shrink-0">
            <div className="mx-auto w-full max-w-3xl px-4 py-4">
              {/* composer：hairline 卡 + 主色 focus 环，实心主色发送钮（DESIGN.md composer）。
                  身份为只读徽章——P21 起 role 服务端权威（users.role），不可在客户端切换 */}
              <div className="flex items-end gap-2 rounded-2xl border bg-card p-2 shadow-sm transition-[border-color,box-shadow] duration-150 ease-out-expo focus-within:border-primary/60 focus-within:shadow-md focus-within:ring-4 focus-within:ring-primary/10">
                {user && (
                  <span
                    className="h-9 shrink-0 rounded-lg bg-primary/10 px-3 text-xs font-medium leading-9 text-primary"
                    title={`${user.display_name || user.email} · 权限随账号角色`}
                  >
                    {ROLE_LABEL[user.role] ?? user.role}身份
                  </span>
                )}
                <textarea
                  id="composer-input"
                  value={input}
              placeholder={
                currentSession ? `试试：${ROTATING_EXAMPLES[exampleIdx]}` : "正在准备会话…"
              }
                  rows={1}
                  className="t-body max-h-40 min-h-9 flex-1 resize-none border-0 bg-transparent px-1 py-2 outline-none placeholder:text-muted-foreground"
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

          <footer className="t-meta shrink-0 px-4 pb-2.5 text-center text-muted-foreground/80">
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
            className="fixed inset-y-0 left-0 z-50 flex w-72 flex-col bg-sidebar md:hidden"
            role="dialog"
            aria-label="导航与会话列表"
          >
            <div className="flex h-13 shrink-0 items-center justify-between px-3">
              <span className="flex items-center gap-2">
                <span
                  className="flex size-7 items-center justify-center rounded-md bg-primary font-display text-sm font-semibold text-primary-foreground"
                  aria-hidden
                >
                  格
                </span>
                <span className="font-display text-[15px] font-semibold">格物</span>
              </span>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setDrawerOpen(false)}
                aria-label="关闭菜单"
              >
                <X className="size-4" aria-hidden />
              </Button>
            </div>
            <div className="px-2 pt-1">
              <NavColumn />
            </div>
            <div className="flex items-center justify-between gap-2 px-3 pt-2 md:hidden">
              <UserMenu />
              <div className="flex items-center gap-0.5">
                <GithubLink />
                <ThemeToggle />
              </div>
            </div>
            <div className="mt-2 min-h-0 flex-1 overflow-y-auto border-t pt-2">
              {sessionList}
            </div>
          </aside>
        </>
      )}
    </main>
  );
}
