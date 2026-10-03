/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // P37：dev 与 prod 分目录。此前共用 .next，任何一次 next build 都会把 dev
  // server 的客户端 chunk 覆盖成 404（P19/P20/P37 三次复发；症状=页面没有
  // hydration，动效全停在首帧，看起来"改了个寂寞"）。dev 走 .next-dev。
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // P21：/api 同源代理到 FastAPI（浏览器与 API 同源，cookie 会话无跨域依赖）。
  // P30：rewrites 代理层对 SSE 全量缓冲（dev 与 next start 实测整段一坨到达），
  // 改由 app/api/[...path]/route.ts Route Handler 真流式透传，此处的 rewrites
  // 退役。API_PROXY_TARGET 缺省 127.0.0.1:8000，生产由部署环境注入。
};

export default nextConfig;
