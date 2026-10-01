import type { Metadata } from "next";
import { Geist, Geist_Mono, Noto_Serif_SC } from "next/font/google";
import Link from "next/link";
import Nav from "@/components/nav";
import ThemeToggle from "@/components/theme-toggle";
import { ThemeProvider } from "@/components/theme-provider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import "./globals.css";

// 字体双轨（DESIGN.md）：衬线 display 走 Noto Serif SC（标题/品牌/空态标语/回答内标题），
// 正文 Geist + 系统 CJK 黑体栈（--font-sans 在 globals.css @theme 里拼接），mono 用于单号/延迟。
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
