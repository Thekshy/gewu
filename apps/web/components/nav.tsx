"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useUser } from "@/lib/auth";
import { cn } from "@/lib/utils";

/**
 * 导航（P37 产品收敛）：**按受众分两组**。
 *
 * 用户问「这产品主要给谁用」的答案是「真实学生」——所以主导航只留学生真正会用的
 * 两项（对话 / 我的办理）；控制台、记忆这类「系统观察面」从主航降级到侧栏底部的
 * 「系统」分组，视觉上分层，不再和用户功能并列（此前四项混排，对第一次点进来的
 * 学生，「控制台」「记忆」是零信息量的词）。
 */
const USER_ITEMS = [
  { href: "/", label: "对话" },
  { href: "/records", label: "我的办理" },
];

const SYSTEM_ITEMS = [
  { href: "/console", label: "控制台" },
  { href: "/memory", label: "记忆" },
];

/** 「管理」仅 admin 渲染（P23）；游客隐藏系统观察面（P39：记忆/控制台登录后解锁）。 */
function useNavGroups() {
  const { user } = useUser();
  if (user?.role === "guest") return { userItems: USER_ITEMS, systemItems: [] };
  const system =
    user?.role === "admin" ? [...SYSTEM_ITEMS, { href: "/admin", label: "管理" }] : SYSTEM_ITEMS;
  return { userItems: USER_ITEMS, systemItems: system };
}

/** 顶栏横排（工具页细顶栏用；chat 页无顶栏——P35 app-shell 拨盘分层）。 */
export function NavRow() {
  const pathname = usePathname();
  const { userItems, systemItems } = useNavGroups();
  const item = (it: { href: string; label: string }, quiet = false) => {
    const active = pathname === it.href;
    return (
      <Link
        key={it.href}
        href={it.href}
        aria-current={active ? "page" : undefined}
        className={cn(
          "t-small rounded-md px-2.5 py-1.5 font-medium transition-colors duration-150 ease-out-expo",
          active
            ? "bg-accent text-foreground"
            : quiet
              ? "text-muted-foreground/80 hover:bg-muted hover:text-foreground"
              : "text-muted-foreground hover:bg-muted hover:text-foreground",
        )}
      >
        {it.label}
      </Link>
    );
  };
  return (
    <nav className="flex items-center gap-0.5" aria-label="页面导航">
      {userItems.map((it) => item(it))}
      {systemItems.length > 0 && (
        <>
          <span className="mx-1.5 h-4 w-px bg-border" aria-hidden />
          {systemItems.map((it) => item(it, true))}
        </>
      )}
    </nav>
  );
}

/** 侧栏竖排（chat app 侧栏与移动抽屉共用；DESIGN.md app-shell）。 */
export function NavColumn() {
  const pathname = usePathname();
  const { userItems, systemItems } = useNavGroups();
  const row = (it: { href: string; label: string }) => {
    const active = pathname === it.href;
    return (
      <Link
        key={it.href}
        href={it.href}
        aria-current={active ? "page" : undefined}
        className={cn(
          "t-small rounded-md px-3 py-2 font-medium transition-colors duration-150 ease-out-expo",
          active
            ? "bg-accent text-foreground"
            : "text-muted-foreground hover:bg-muted hover:text-foreground",
        )}
      >
        {it.label}
      </Link>
    );
  };
  return (
    <nav className="flex flex-col" aria-label="页面导航">
      <div className="flex flex-col gap-0.5">{userItems.map(row)}</div>
      {/* 系统观察面：与用户功能分层，不抢主航位置（游客隐藏，P39） */}
      {systemItems.length > 0 && (
        <>
          <p className="t-meta mt-4 px-3 pb-1 font-semibold text-muted-foreground/70">系统</p>
          <div className="flex flex-col gap-0.5">{systemItems.map(row)}</div>
        </>
      )}
    </nav>
  );
}
