import type { Metadata } from "next";
import { Geist, Geist_Mono, Noto_Serif_SC } from "next/font/google";
import AppHeader from "@/components/app-header";
import RouteFade from "@/components/route-fade";
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
  title: "格物 · 校园智能服务平台",
  description:
    "格物 Gewu：面向高校场景的智能体平台——制度问答、业务办理与长期记忆（演示数据为虚构的「钱塘大学」）",
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
            {/* P35 app-shell：顶栏条件渲染——chat 页 chrome-less（导航在 page 侧栏），
                工具页 44px 细顶栏。旧 52px 全站厚顶栏退役（DESIGN.md app-shell）。 */}
            <AppHeader />
            <RouteFade>{children}</RouteFade>
          </TooltipProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
