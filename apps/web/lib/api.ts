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

/** GET /api/business/overview：业务台账。 */
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
  bookings: BookingFull[];
  tickets: TicketView[];
}

export type Role = "student" | "counselor";

/** chat 请求 mode：auto（agent-first 主循环）/classic（级联路由基线）/direct/research/react（=auto，P17 合并）。 */
export type ChatMode = "auto" | "direct" | "research" | "react" | "classic";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

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
  const res = await fetch(`${API_BASE}/api/docs`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as DocInfo[];
}

export async function fetchBusinessOverview(): Promise<BusinessOverview> {
  const res = await fetch(`${API_BASE}/api/business/overview`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as BusinessOverview;
}

export async function businessReset(): Promise<void> {
  const res = await fetch(`${API_BASE}/api/business/reset`, { method: "POST" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function search(query: string, k = 5): Promise<SearchHit[]> {
  const res = await fetch(`${API_BASE}/api/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, k }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `HTTP ${res.status}`);
  }
  return (await res.json()) as SearchHit[];
}

export interface ChatOpts {
  sessionId?: string;
  role?: Role;
}

/** 调用 /api/chat 的 SSE 流，逐事件回调。 */
export async function streamChat(
  question: string,
  mode: ChatMode,
  onEvent: (ev: ChatEvent) => void,
  opts: ChatOpts = {},
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      mode,
      session_id: opts.sessionId ?? "default",
      role: opts.role ?? "student",
    }),
  });
  if (!res.ok || !res.body) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `请求失败（HTTP ${res.status}）`);
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
