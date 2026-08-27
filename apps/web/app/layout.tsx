import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "格物 · 校园智能问答",
  description:
    "格物 Gewu：高校场景 Deep Research 智能问答系统（演示数据为虚构的「钱塘大学」）",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
