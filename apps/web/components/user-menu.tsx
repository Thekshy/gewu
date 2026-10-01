"use client";

import Link from "next/link";
import { LogOut } from "lucide-react";
import { logoutAndRedirect, useUser } from "@/lib/auth";
import { ROLE_LABEL } from "@/lib/labels";
import { Button, buttonVariants } from "@/components/ui/button";

/** 顶栏右侧登录态：未登录=登录入口；已登录=昵称+角色徽章+登出。 */
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
