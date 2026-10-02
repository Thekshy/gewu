// P30：/api 手写透传（P21 预留退路转正）。
//
// next.config rewrites 的代理层对 text/event-stream 全量缓冲（15.3.3 实测
// dev 与 next start 都是整段一坨到达），agent 流式逐字体验被吃掉——改由
// Route Handler 直转上游 body（ReadableStream 管道，真流式透传），SSE 帧
// 到达即转发。cookie 会话经同源转发无跨域依赖（与 P21 同源形态不变）。
const TARGET = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";

const HOP_BY_HOP = new Set([
  "host",
  "connection",
  "content-length",
  "transfer-encoding",
  "keep-alive",
  "upgrade",
]);

async function proxy(req: Request): Promise<Response> {
  const suffix = new URL(req.url).pathname.replace(/^\/api\/?/, "");
  const upstream = await fetch(`${TARGET}/api/${suffix}${new URL(req.url).search}`, {
    method: req.method,
    headers: new Headers(
      [...req.headers].filter(([k]) => !HOP_BY_HOP.has(k.toLowerCase())),
    ),
    body: ["GET", "HEAD"].includes(req.method) ? undefined : await req.arrayBuffer(),
    redirect: "manual",
    cache: "no-store",
  });
  const headers = new Headers();
  [...upstream.headers].forEach(([k, v]) => {
    const lower = k.toLowerCase();
    if (HOP_BY_HOP.has(lower) || lower === "content-encoding" || lower === "set-cookie") return;
    headers.append(k, v);
  });
  // set-cookie 多值经 Headers 迭代会被合并，须逐条重放（登录态依赖）
  upstream.headers.getSetCookie().forEach((c) => headers.append("set-cookie", c));
  return new Response(upstream.body, { status: upstream.status, headers });
}

export const dynamic = "force-dynamic";

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;