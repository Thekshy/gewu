export interface Citation {
  n: number;
  doc_id: string;
  title: string;
  source: string;
}

export interface Step {
  index: number;
  subquestion: string;
  sources: string[];
}

export interface PendingAction {
  tool: string;
  label: string;
  args: Record<string, string>;
}

export interface ActionResult {
  tool: string;
  success: boolean;
  message: string;
  receipt?: string | null;
}

/** PARITY §3：done.reason 四值（P10 起可选下发，老事件缺省 completed 语义）。 */
export type DoneReason = "completed" | "max_tokens" | "error" | "aborted";

export interface ChatEvent {
  type: string;
  [key: string]: unknown;
}

export interface HealthInfo {
  status: string;
  version: string;
  llm: boolean;
  embeddings: boolean;
  docs: number;
  chunks: number;
  budget: { used: number; limit: number };
}

/** GET /api/docs：已入库文档元信息。 */
export interface DocInfo {
  doc_id: string;
  title: string;
  source: string;
  updated: string;
  chunks: number;
}

/** POST /api/search：混合检索命中（文本截断 300 字，服务端行为）。 */
export interface SearchHit {
  doc_id: string;
  title: string;
  source: string;
  seq: number;
  text: string;
}

/** GET /api/business/overview：业务台账（P21 起本人视图，admin ?all=1 全量）。 */
export interface BookingFull {
  booking_id: string;
  venue: string;
  date: string;
  slot: string;
  user: string;
}

export interface TicketView {
  ticket: string;
  user: string;
  leave_type: string;
  start: string;
  end: string;
  days: number;
  approver: string;
  status: string;
}

export interface BusinessOverview {
  scope?: "mine" | "all";
  bookings: BookingFull[];
  tickets: TicketView[];
}

/** P21：登录用户（role 服务端权威，请求侧不再传 role）。 */
export interface User {
  email: string;
  display_name: string;
  role: "student" | "counselor" | "admin";
}

/** chat 请求 mode：auto（agent-first 主循环）/classic（级联路由基线）/direct/research/react（=auto，P17 合并）。 */
export type ChatMode = "auto" | "direct" | "research" | "react" | "classic";

/**
 * P21：默认同源相对路径——浏览器请求发给 next 自身，经 next.config rewrites
 * 代理到 FastAPI（同源 cookie 会话零跨域配置）。NEXT_PUBLIC_API_BASE 保留
 * 给直连调试（如绕过代理打 8000）。
 */
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

/**
 * 带错误体解析的 fetch 包装；401 统一跳登录（会话过期/未登录）。
 * 契约：path 只传路径（"/api/..."），API_BASE 由本函数统一拼接——
 * 调用方自带 `${API_BASE}` 会双拼出非法主机名（线上 10-01 事故根因）。
 */
export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (res.status === 401 && typeof window !== "undefined") {
    if (!window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
    throw new Error("未登录或会话已过期");
  }
  return res;
}

async function detailOf(res: Response): Promise<string> {
  const body = await res.json().catch(() => null);
  return body?.detail ?? `请求失败（HTTP ${res.status}）`;
}

// ---------- 认证（P21） ----------

/** 登录态变更广播（login/register 成功后 dispatch）：各 useUser 实例监听重拉。
 *  Nav 在 layout 不随客户端导航 remount，没有它右上角会停留在旧的未登录态。 */
export const AUTH_CHANGED_EVENT = "gewu:auth";

function broadcastAuthChanged() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
}

export async function fetchMe(): Promise<User | null> {
  const res = await fetch(`${API_BASE}/api/auth/me`);
  if (res.status === 401) return null; // 确认未登录
  if (!res.ok) throw new Error(`HTTP ${res.status}`); // 429/5xx 是临时失败，不能当未登录
  return (await res.json()) as User;
}

export async function login(email: string, password: string): Promise<User> {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error(await detailOf(res));
  broadcastAuthChanged();
  return (await res.json()) as User;
}

export async function register(
  email: string,
  password: string,
  inviteCode: string,
): Promise<User> {
  const res = await fetch(`${API_BASE}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, invite_code: inviteCode }),
  });
  if (!res.ok) throw new Error(await detailOf(res));
  broadcastAuthChanged();
  return (await res.json()) as User;
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE}/api/auth/logout`, { method: "POST" }).catch(() => {});
}

// ---------- 会话（P22：会话为服务端资源，CRUD + 历史恢复） ----------

export type SessionKind = "chat" | "compare";

export interface SessionInfo {
  session_id: string;
  title: string;
  kind: SessionKind;
  created_at: string;
  updated_at: string;
}

/** 历史恢复的对话级消息（事件级细节不恢复，纯文本）。 */
export interface HistoryMessage {
  role: "user" | "assistant";
  text: string;
}

export async function createSession(kind: SessionKind = "chat"): Promise<SessionInfo> {
  const res = await apiFetch(`/api/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind }),
  });
  if (!res.ok) throw new Error(await detailOf(res));
  return (await res.json()) as SessionInfo;
}

export async function listSessions(kind?: SessionKind): Promise<SessionInfo[]> {
  const res = await apiFetch(`/api/sessions${kind ? `?kind=${kind}` : ""}`);
  if (!res.ok) throw new Error(await detailOf(res));
  return (await res.json()) as SessionInfo[];
}

export async function renameSession(id: string, title: string): Promise<SessionInfo> {
  const res = await apiFetch(`/api/sessions/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(await detailOf(res));
  return (await res.json()) as SessionInfo;
}

export async function deleteSession(id: string): Promise<void> {
  const res = await apiFetch(`/api/sessions/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await detailOf(res));
}

export async function fetchMessages(id: string): Promise<HistoryMessage[]> {
  const res = await apiFetch(`/api/sessions/${id}/messages`);
  if (!res.ok) throw new Error(await detailOf(res));
  const body = (await res.json()) as { messages: HistoryMessage[] };
  return body.messages ?? [];
}

// ---------- 长期记忆（P22：fact 用户可见可管） ----------

export type FactKind = "profile" | "preference" | "constraint";

export interface Fact {
  kind: FactKind;
  key: string;
  value: string;
}

export async function listFacts(): Promise<Fact[]> {
  const res = await apiFetch(`/api/memory/facts`);
  if (!res.ok) throw new Error(await detailOf(res));
  return (await res.json()) as Fact[];
}

export async function upsertFact(fact: Fact): Promise<void> {
  const res = await apiFetch(`/api/memory/facts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fact),
  });
  if (!res.ok) throw new Error(await detailOf(res));
}

export async function deleteFact(kind: FactKind, key: string): Promise<void> {
  const res = await apiFetch(
    `/api/memory/facts?kind=${encodeURIComponent(kind)}&key=${encodeURIComponent(key)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(await detailOf(res));
}

// ---------- 消息反馈（P25：america.gov Good/Bad response 同款，upsert 覆盖语义） ----------

export type FeedbackRating = "good" | "bad";

export async function sendFeedback(
  sessionId: string,
  question: string,
  rating: FeedbackRating,
): Promise<void> {
  const res = await apiFetch(`/api/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, question, rating }),
  });
  if (!res.ok) throw new Error(await detailOf(res));
}

// ---------- 管理后台（P23：均 admin） ----------

export interface AdminStats {
  users: number;
  sessions: number;
  invites: number;
  chat_sessions: number;
  today_tokens: number;
  budget: { used: number; limit: number };
}

export interface AdminUser {
  email: string;
  display_name: string;
  role: "student" | "counselor" | "admin";
  status: "active" | "disabled";
  daily_token_limit: number | null;
  today_tokens: number;
}

export interface AdminInvite {
  code: string;
  max_uses: number;
  used_count: number;
  expires_at: string | null;
  note: string;
  created_at: string;
  created_by: string;
}

export interface AdminSessionRow {
  session_id: string;
  user: string;
  title: string;
  kind: "chat" | "compare";
  updated_at: string;
}

export interface AdminUsage {
  daily: { day: string; tokens: number }[];
  today_top: { user: string; tokens: number }[];
}

export async function fetchAdminStats(): Promise<AdminStats> {
  const res = await apiFetch(`/api/admin/stats`);
  if (!res.ok) throw new Error(await detailOf(res));
  return (await res.json()) as AdminStats;
}

export async function listAdminUsers(): Promise<AdminUser[]> {
  const res = await apiFetch(`/api/admin/users`);
  if (!res.ok) throw new Error(await detailOf(res));
  return (await res.json()) as AdminUser[];
}

export async function updateAdminUser(
  email: string,
  patch: Partial<Pick<AdminUser, "role" | "status">> & { daily_token_limit?: number | null },
): Promise<AdminUser> {
  const res = await apiFetch(`/api/admin/users/${encodeURIComponent(email)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(await detailOf(res));
  return (await res.json()) as AdminUser;
}

export async function listAdminInvites(): Promise<AdminInvite[]> {
  const res = await apiFetch(`/api/admin/invites`);
  if (!res.ok) throw new Error(await detailOf(res));
  return (await res.json()) as AdminInvite[];
}

export async function createAdminInvite(uses: number, days?: number, note = ""): Promise<string> {
  const res = await apiFetch(`/api/admin/invites`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ uses, days, note }),
  });
  if (!res.ok) throw new Error(await detailOf(res));
  return ((await res.json()) as { code: string }).code;
}

export async function listAdminSessions(
  kind?: string,
  q?: string,
): Promise<AdminSessionRow[]> {
  const params = new URLSearchParams();
  if (kind) params.set("kind", kind);
  if (q) params.set("q", q);
  const qs = params.toString();
  const res = await apiFetch(`/api/admin/sessions${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(await detailOf(res));
  return (await res.json()) as AdminSessionRow[];
}

export async function deleteAdminSession(id: string): Promise<void> {
  const res = await apiFetch(`/api/admin/sessions/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await detailOf(res));
}

export async function fetchAdminUsage(days = 7): Promise<AdminUsage> {
  const res = await apiFetch(`/api/admin/usage?days=${days}`);
  if (!res.ok) throw new Error(await detailOf(res));
  return (await res.json()) as AdminUsage;
}

// ---------- 业务端点 ----------

export async function fetchHealth(): Promise<HealthInfo | null> {
  try {
    const res = await fetch(`${API_BASE}/api/health`);
    if (!res.ok) return null;
    return (await res.json()) as HealthInfo;
  } catch {
    return null;
  }
}

export async function fetchDocs(): Promise<DocInfo[]> {
  const res = await apiFetch(`/api/docs`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as DocInfo[];
}

export async function fetchBusinessOverview(all = false): Promise<BusinessOverview> {
  const res = await apiFetch(
    `/api/business/overview${all ? "?all=1" : ""}`,
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as BusinessOverview;
}

export async function businessReset(): Promise<void> {
  const res = await apiFetch(`/api/business/reset`, { method: "POST" });
  if (!res.ok) throw new Error(await detailOf(res));
}

export async function search(query: string, k = 5): Promise<SearchHit[]> {
  const res = await apiFetch(`/api/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, k }),
  });
  if (!res.ok) throw new Error(await detailOf(res));
  return (await res.json()) as SearchHit[];
}

export interface ChatOpts {
  sessionId: string;
}

/** 调用 /api/chat 的 SSE 流，逐事件回调（P21：登录态由同源 cookie 携带；
 * P22：sessionId 必传——会话须先经 POST /api/sessions 登记属本人）。 */
export async function streamChat(
  question: string,
  mode: ChatMode,
  onEvent: (ev: ChatEvent) => void,
  opts: ChatOpts,
): Promise<void> {
  const res = await apiFetch(`/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      mode,
      session_id: opts.sessionId,
    }),
  });
  if (!res.ok || !res.body) {
    throw new Error(await detailOf(res));
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(6)));
      } catch {
        // 忽略不完整的事件
      }
    }
  }
}

