"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { NavRow } from "@/components/nav";
import ThemeToggle from "@/components/theme-toggle";
import UserMenu from "@/components/user-menu";

/** 全站条件顶栏（P35 app-shell）：chat 页 chrome-less（导航在 app 侧栏），
 *  其余页（console/memory/admin/login）渲染 44px 细顶栏——拨盘分层：
 *  品牌时刻极简、工具页保工具导航。 */
export default function AppHeader() {
  const pathname = usePathname();
  if (pathname === "/") return null;
  return (
    <header className="flex h-11 shrink-0 items-center gap-3 border-b px-4">
      <Link href="/" className="flex items-center gap-2">
        <span
          className="flex size-6 items-center justify-center rounded-md bg-primary font-display text-xs font-semibold text-primary-foreground shadow-sm"
          aria-hidden
        >
          格
        </span>
        <span className="font-display text-sm font-semibold">格物</span>
      </Link>
      <NavRow />
      <div className="ml-auto flex items-center gap-2">
        <UserMenu />
        <ThemeToggle />
      </div>
    </header>
  );
}
