"use client";

import Link from "next/link";
import { LogIn, LogOut } from "lucide-react";
import { logoutAndRedirect, useUser } from "@/lib/auth";
import { ROLE_LABEL } from "@/lib/labels";
import { Button, buttonVariants } from "@/components/ui/button";

/** 侧栏底部登录态：未登录=登录入口；已登录=昵称+角色徽章+登出。
 *  游客（P39）=徽章 + 登录升级入口——一次性身份，登出无意义，引导转正。 */
export default function UserMenu() {
  const { user, loading } = useUser();
  if (loading) {
    return <span className="hidden h-5 w-20 animate-pulse rounded bg-muted sm:block" aria-hidden />;
  }
  if (!user) {
    return (
      <Link href="/login" className={buttonVariants({ variant: "outline", size: "sm" })}>
        登录
      </Link>
    );
  }
  if (user.role === "guest") {
    return (
      <div className="flex items-center gap-2">
        <span
          className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary"
          title="免登录游客身份，数据短期保留"
        >
          {ROLE_LABEL[user.role] ?? user.role}
        </span>
        <Link
          href="/login"
          className={buttonVariants({ variant: "outline", size: "sm" })}
          title="登录后解锁长期记忆等完整功能"
        >
          <LogIn className="size-4" aria-hidden />
          登录
        </Link>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <span className="hidden text-sm text-muted-foreground sm:inline" title={user.email}>
        {user.display_name || user.email}
      </span>
      <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
        {ROLE_LABEL[user.role] ?? user.role}
      </span>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="登出"
        title="登出"
        onClick={() => void logoutAndRedirect()}
      >
        <LogOut className="size-4" aria-hidden />
      </Button>
    </div>
  );
}
