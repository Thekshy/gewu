"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useUser } from "@/lib/auth";
import { cn } from "@/lib/utils";

const ITEMS = [
  { href: "/", label: "对话" },
  { href: "/console", label: "控制台" },
  { href: "/memory", label: "记忆" },
];

/** 「管理」仅 admin 渲染（P23）。 */
function useNavItems() {
  const { user } = useUser();
  return user?.role === "admin" ? [...ITEMS, { href: "/admin", label: "管理" }] : ITEMS;
}

/** 顶栏横排（工具页细顶栏用；chat 页无顶栏——P35 app-shell 拨盘分层）。 */
export function NavRow() {
  const pathname = usePathname();
  const items = useNavItems();
  return (
    <nav className="flex items-center gap-1" aria-label="页面导航">
      {items.map((it) => {
        const active = pathname === it.href;
        return (
          <Link
            key={it.href}
            href={it.href}
            aria-current={active ? "page" : undefined}
            className={
              "rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors " +
              (active
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground")
            }
          >
            {it.label}
          </Link>
        );
      })}
    </nav>
  );
}

/** 侧栏竖排（chat app 侧栏与移动抽屉共用；DESIGN.md app-shell）。 */
export function NavColumn() {
  const pathname = usePathname();
  const items = useNavItems();
  return (
    <nav className="flex flex-col gap-0.5" aria-label="页面导航">
      {items.map((it) => {
        const active = pathname === it.href;
        return (
          <Link
            key={it.href}
            href={it.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "rounded-lg px-3 py-2 text-sm font-medium transition-colors duration-150 ease-out-expo",
              active
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            )}
          >
            {it.label}
          </Link>
        );
      })}
    </nav>
  );
}

/** 兼容旧引用（AppHeader 已改用 NavRow）。 */
export default NavRow;
