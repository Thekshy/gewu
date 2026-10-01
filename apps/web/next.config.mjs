/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // P21：/api 同源代理到 FastAPI（浏览器与 API 同源，cookie 会话无跨域依赖；
  // SSE 流式透传在此链路上验证，必要时退路为手写 app/api/[...path] 透传）。
  // API_PROXY_TARGET 缺省 127.0.0.1:8000，生产由部署环境注入。
  async rewrites() {
    const target = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${target}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
