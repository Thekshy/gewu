import type { Metadata } from "next";
import { Geist, Geist_Mono, Noto_Serif_SC } from "next/font/google";
import Link from "next/link";
import Nav from "@/components/nav";
import ThemeToggle from "@/components/theme-toggle";
import UserMenu from "@/components/user-menu";
import { ThemeProvider } from "@/components/theme-provider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import "./globals.css";

// 字体契约（DESIGN.md 衬线域）：Noto Serif SC 只服务三个内容时刻（空态标语/引用块/
// 回答内文档标题）+ 品牌方印与字标；UI chrome（导航/按钮/标签/页面 h1/CardTitle）全 sans。
const geist = Geist({ subsets: ["latin"], variable: "--font-geist" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" });
const notoSerif = Noto_Serif_SC({
  subsets: ["latin"],
  weight: ["400", "600"],
  variable: "--font-noto-serif",
  display: "swap",
});

export const metadata: Metadata = {
  title: "格物 · 校园智能问答",
  description:
    "格物 Gewu：高校场景 Deep Research 智能问答系统（演示数据为虚构的「钱塘大学」）",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // suppressHydrationWarning：next-themes 在首帧前写 <html class>，避免 SSR 水合告警
    <html
      lang="zh-CN"
      suppressHydrationWarning
      className={cn("font-sans", geist.variable, geistMono.variable, notoSerif.variable)}
    >
      <body className="flex h-dvh flex-col bg-background text-foreground">
        <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false} disableTransitionOnChange>
          <TooltipProvider delay={200}>
            <header className="flex h-13 shrink-0 items-center gap-3 border-b px-4">
              <Link href="/" className="flex items-center gap-2.5">
                {/* 品牌方印：全站唯一固定用 primary 实底的识别位（DESIGN.md brand-mark） */}
                <span
                  className="flex size-7 items-center justify-center rounded-md bg-primary font-display text-sm font-semibold text-primary-foreground shadow-sm"
                  aria-hidden
                >
                  格
                </span>
                <span className="font-display text-[15px] font-semibold">格物</span>
                <span className="hidden text-xs text-muted-foreground sm:inline">校园智能问答</span>
              </Link>
              <Nav />
              <div className="ml-auto flex items-center gap-2">
                <UserMenu />
                <ThemeToggle />
              </div>
            </header>
            <div className="min-h-0 flex-1">{children}</div>
          </TooltipProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
