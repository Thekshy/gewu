"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useUser } from "@/lib/auth";

const ITEMS = [
  { href: "/", label: "对话" },
  { href: "/compare", label: "对比实验" },
  { href: "/console", label: "控制台" },
  { href: "/memory", label: "记忆" },
];

/** 全局顶栏导航：视图 pill 切换，当前项高亮；「管理」仅 admin 渲染（P23）。 */
export default function Nav() {
  const pathname = usePathname();
  const { user } = useUser();
  const items = user?.role === "admin" ? [...ITEMS, { href: "/admin", label: "管理" }] : ITEMS;
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
