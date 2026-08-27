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

/** 调用 /api/chat 的 SSE 流，逐事件回调。 */
export async function streamChat(
  question: string,
  mode: "auto" | "direct" | "research",
  onEvent: (ev: ChatEvent) => void,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, mode }),
  });
  if (!res.ok || !res.body) {
    throw new Error(`请求失败（HTTP ${res.status}）`);
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
