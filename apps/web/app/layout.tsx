import type { Metadata } from "next";
import { Geist } from "next/font/google";
import Link from "next/link";
import Nav from "@/components/nav";
import ThemeToggle from "@/components/theme-toggle";
import { ThemeProvider } from "@/components/theme-provider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import "./globals.css";

const geist = Geist({ subsets: ["latin"], variable: "--font-sans" });

export const metadata: Metadata = {
  title: "格物 · 校园智能问答",
  description:
    "格物 Gewu：高校场景 Deep Research 智能问答系统（演示数据为虚构的「钱塘大学」）",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // suppressHydrationWarning：next-themes 在首帧前写 <html class>，避免 SSR 水合告警
    <html lang="zh-CN" suppressHydrationWarning className={cn("font-sans", geist.variable)}>
      <body className="flex h-dvh flex-col bg-background text-foreground">
        <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false} disableTransitionOnChange>
          <TooltipProvider delay={200}>
            <header className="flex h-13 shrink-0 items-center gap-3 border-b px-4">
              <Link href="/" className="flex items-center gap-2.5">
                <span
                  className="flex size-7 items-center justify-center rounded-lg bg-gradient-to-br from-cyan-600 to-teal-700 text-sm font-semibold text-white shadow-sm"
                  aria-hidden
                >
                  格
                </span>
                <span className="text-sm font-semibold">格物</span>
                <span className="hidden text-xs text-muted-foreground sm:inline">校园智能问答</span>
              </Link>
              <Nav />
              <div className="ml-auto flex items-center">
                <ThemeToggle />
              </div>
            </header>
            <div className="min-h-0 flex-1">{children}</div>
            {/* 全页胶片颗粒：3% 噪点叠加，质感层（globals.css .grain-overlay） */}
            <div aria-hidden className="grain-overlay" />
          </TooltipProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
