"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, LogIn, TriangleAlert, UserPlus } from "lucide-react";
import { login, register } from "@/lib/api";
import { useUser } from "@/lib/auth";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

// 登录/注册（P21 邀请码封闭注册·内测）：同源代理链路，cookie 会话由服务端下发。
// 单卡单层容器 + hairline 分隔（DESIGN.md：无嵌套卡片、无光斑、无冷色）。

type Mode = "login" | "register";

export default function LoginPage() {
  const router = useRouter();
  const { user, loading } = useUser();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!loading && user) router.replace("/"); // 已登录直达对话页
  }, [loading, user, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setErr("");
    setBusy(true);
    try {
      if (mode === "login") {
        await login(email.trim(), password);
      } else {
        await register(email.trim(), password, inviteCode.trim());
      }
      router.replace("/");
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex h-full items-center justify-center overflow-y-auto px-4">
      <div className="w-full max-w-sm space-y-6 py-10">
        <div className="space-y-1.5 text-center">
          <h1 className="text-xl font-semibold">{mode === "login" ? "登录格物" : "注册内测账号"}</h1>
          <p className="text-sm text-muted-foreground">
            {mode === "login"
              ? "校园制度问答 · 场馆预约 · 请假办理"
              : "内测采用邀请码封闭注册，请联系管理员获取"}
          </p>
        </div>

        <form onSubmit={submit} className="space-y-4 rounded-2xl border bg-card p-5 shadow-sm">
          <div className="grid grid-cols-2 gap-1 rounded-lg bg-muted p-1" role="tablist" aria-label="登录或注册">
            {(
              [
                ["login", "登录"],
                ["register", "注册"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={mode === value}
                onClick={() => {
                  setMode(value);
                  setErr("");
                }}
                className={
                  "rounded-md px-3 py-1.5 text-sm font-medium transition-colors " +
                  (mode === value
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground")
                }
              >
                {label}
              </button>
            ))}
          </div>

          <div className="space-y-1.5">
            <label htmlFor="email" className="text-sm font-medium">
              邮箱
            </label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="password" className="text-sm font-medium">
              密码
            </label>
            <Input
              id="password"
              type="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              placeholder="至少 8 位"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          {mode === "register" && (
            <div className="space-y-1.5">
              <label htmlFor="invite" className="text-sm font-medium">
                邀请码
              </label>
              <Input
                id="invite"
                placeholder="内测邀请码"
                value={inviteCode}
                onChange={(e) => setInviteCode(e.target.value)}
                required
              />
            </div>
          )}

          {err && (
            <Alert variant="destructive" className="py-2.5">
              <TriangleAlert className="size-4" aria-hidden />
              <AlertDescription>{err}</AlertDescription>
            </Alert>
          )}

          <Button type="submit" disabled={busy} className="h-9 w-full">
            {busy ? (
              <Loader2 className="animate-spin" aria-hidden />
            ) : mode === "login" ? (
              <LogIn aria-hidden />
            ) : (
              <UserPlus aria-hidden />
            )}
            {busy ? "提交中…" : mode === "login" ? "登录" : "注册并登录"}
          </Button>
        </form>

        <p className="text-center text-xs text-muted-foreground">
          演示语料与业务系统均为虚构的「钱塘大学」合成数据
        </p>
      </div>
    </main>
  );
}
