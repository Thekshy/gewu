"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [
  { href: "/", label: "对话" },
  { href: "/compare", label: "对比实验" },
  { href: "/console", label: "控制台" },
];

/** 顶部导航：三视图切换，当前项高亮。 */
export default function Nav() {
  const pathname = usePathname();
  return (
    <nav className="nav" aria-label="页面导航">
      {ITEMS.map((it) => (
        <Link
          key={it.href}
          href={it.href}
          className={`nav-item${pathname === it.href ? " active" : ""}`}
        >
          {it.label}
        </Link>
      ))}
    </nav>
  );
}
