"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Loader2, Plus, Trash2, TriangleAlert, X } from "lucide-react";
import {
  createAdminInvite,
  deleteAdminSession,
  fetchAdminStats,
  fetchAdminUsage,
  listAdminInvites,
  listAdminSessions,
  listAdminUsers,
  updateAdminUser,
  type AdminInvite,
  type AdminSessionRow,
  type AdminStats,
  type AdminUsage,
  type AdminUser,
} from "@/lib/api";
import { useRequireAdmin } from "@/lib/auth";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

// 管理后台（P23）：用户/邀请码/会话巡查/用量四区。范式沿 console 页的
// Operate 惯例——素 Card 面板 + Table divide-y，无嵌套卡（DESIGN.md kit-规则）。

const ROLE_LABEL: Record<string, string> = {
  student: "学生",
  counselor: "辅导员",
  admin: "管理员",
};

function fmtTokens(n: number): string {
  return n >= 10_000 ? `${(n / 10_000).toFixed(1)} 万` : String(n);
}

export default function AdminPage() {
  const { user } = useRequireAdmin();
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [invites, setInvites] = useState<AdminInvite[] | null>(null);
  const [sessions, setSessions] = useState<AdminSessionRow[] | null>(null);
  const [usage, setUsage] = useState<AdminUsage | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // 用户表行内编辑（限额）
  const [editingLimit, setEditingLimit] = useState<string | null>(null);
  const [limitDraft, setLimitDraft] = useState("");
  // 会话巡查过滤
  const [sessKind, setSessKind] = useState<string>("all");
  const [sessQ, setSessQ] = useState("");
  // 邀请码发放
  const [inviteUses, setInviteUses] = useState("1");
  const [inviteDays, setInviteDays] = useState("");
  const [inviteNote, setInviteNote] = useState("");
  const [inviteBusy, setInviteBusy] = useState(false);
  const [lastCode, setLastCode] = useState<string | null>(null);

  const refreshAll = useCallback(async () => {
    try {
      const [s, u, i, ses, us] = await Promise.all([
        fetchAdminStats(),
        listAdminUsers(),
        listAdminInvites(),
        listAdminSessions(),
        fetchAdminUsage(7),
      ]);
      setStats(s);
      setUsers(u);
      setInvites(i);
      setSessions(ses);
      setUsage(us);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    if (user?.role === "admin") void refreshAll();
  }, [user, refreshAll]);

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await listAdminSessions(sessKind === "all" ? undefined : sessKind, sessQ || undefined));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [sessKind, sessQ]);

  useEffect(() => {
    if (user?.role === "admin") void refreshSessions();
  }, [user, refreshSessions]);

  async function patchUser(email: string, patch: Parameters<typeof updateAdminUser>[1]) {
    try {
      await updateAdminUser(email, patch);
      setUsers(await listAdminUsers());
      setStats(await fetchAdminStats());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function saveLimit(u: AdminUser) {
    const raw = limitDraft.trim();
    setEditingLimit(null);
    if (raw === "") return;
    if (raw === "-") {
      await patchUser(u.email, { daily_token_limit: null }); // 恢复全局缺省
      return;
    }
    const n = Number(raw);
    if (!Number.isInteger(n) || n < 1) {
      setErr("限额需为正整数（或 - 恢复默认）");
      return;
    }
    await patchUser(u.email, { daily_token_limit: n });
  }

  async function issueInvite() {
    const uses = Number(inviteUses);
    const days = inviteDays.trim() === "" ? undefined : Number(inviteDays);
    if (!Number.isInteger(uses) || uses < 1) return setErr("次数需为正整数");
    if (days !== undefined && (!Number.isInteger(days) || days < 1)) return setErr("天数需为正整数");
    setInviteBusy(true);
    try {
      const code = await createAdminInvite(uses, days, inviteNote.trim());
      setLastCode(code);
      setInviteNote("");
      setInvites(await listAdminInvites());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setInviteBusy(false);
    }
  }

  async function removeSession(id: string) {
    try {
      await deleteAdminSession(id);
      await refreshSessions();
      setStats(await fetchAdminStats());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  if (!user || user.role !== "admin") {
    return (
      <main className="flex h-full items-center justify-center text-sm text-muted-foreground" role="status">
        <Loader2 className="mr-2 size-4 animate-spin" aria-hidden />
        正在校验管理员身份…
      </main>
    );
  }

  return (
    <main className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl space-y-5 px-4 py-6">
        <header className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-semibold">管理后台</h1>
            <p className="text-sm text-muted-foreground">
              用户与邀请码、会话巡查、token 用量与限额——内测管理一页承载
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => void refreshAll()}>
            刷新
          </Button>
        </header>

        {err && (
          <Alert variant="destructive" className="py-2.5">
            <TriangleAlert className="size-4" aria-hidden />
            <AlertDescription>
              操作失败：{err}
              <button className="ml-2 underline underline-offset-2" onClick={() => setErr(null)}>
                关闭
              </button>
            </AlertDescription>
          </Alert>
        )}

        {/* 统计卡行：divide-y 面板而非四张卡（craft-floor：cards are the lazy container） */}
        <section aria-label="总览" className="rounded-lg border bg-card shadow-sm">
          {stats === null ? (
            <div className="flex items-center gap-2 px-4 py-6 text-sm text-muted-foreground" role="status">
              <Loader2 className="size-4 animate-spin" aria-hidden /> 加载中…
            </div>
          ) : (
            <dl className="grid grid-cols-2 divide-x sm:grid-cols-5 sm:divide-x">
              {[
                ["用户", String(stats.users)],
                ["会话", String(stats.chat_sessions)],
                ["邀请码", String(stats.invites)],
                ["今日 token", fmtTokens(stats.today_tokens)],
                ["全局预算", `${fmtTokens(stats.budget.used)} / ${fmtTokens(stats.budget.limit)}`],
              ].map(([label, value]) => (
                <div key={label} className="px-4 py-3">
                  <dt className="text-xs text-muted-foreground">{label}</dt>
                  <dd className="mt-0.5 text-lg font-semibold tabular-nums">{value}</dd>
                </div>
              ))}
            </dl>
          )}
        </section>

        {/* 用户表 */}
        <section aria-label="用户管理" className="rounded-lg border bg-card shadow-sm">
          <div className="flex items-baseline gap-2 border-b px-4 py-3">
            <h2 className="text-sm font-semibold">用户</h2>
            <span className="text-xs text-muted-foreground">角色 / 停用（踢下线）/ 个人 token 限额（- 为默认）</span>
          </div>
          {users === null ? (
            <p className="px-4 py-4 text-sm text-muted-foreground">加载中…</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>邮箱</TableHead>
                  <TableHead className="w-32">角色</TableHead>
                  <TableHead className="w-24">状态</TableHead>
                  <TableHead className="w-44">今日用量 / 限额</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {users.map((u) => {
                  const isSelf = u.email === user.email;
                  return (
                    <TableRow key={u.email}>
                      <TableCell>
                        <span className="text-sm">{u.email}</span>
                        {isSelf && <span className="ml-1.5 text-xs text-muted-foreground">（我）</span>}
                      </TableCell>
                      <TableCell>
                        <Select
                          value={u.role}
                          onValueChange={(v) => void patchUser(u.email, { role: v as AdminUser["role"] })}
                          disabled={isSelf}
                        >
                          <SelectTrigger className="h-8 text-xs" aria-label={`${u.email} 的角色`}>
                            <SelectValue>{ROLE_LABEL[u.role]}</SelectValue>
                          </SelectTrigger>
                          <SelectContent>
                            {Object.entries(ROLE_LABEL).map(([v, label]) => (
                              <SelectItem key={v} value={v}>
                                {label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </TableCell>
                      <TableCell>
                        {isSelf ? (
                          <span className="text-xs text-muted-foreground">—</span>
                        ) : (
                          <AlertDialog>
                            <AlertDialogTrigger
                              render={
                                <Button
                                  variant={u.status === "active" ? "outline" : "destructive"}
                                  size="sm"
                                  className="h-8 text-xs"
                                  aria-label="切换停用状态"
                                >
                                  {u.status === "active" ? "停用" : "启用"}
                                </Button>
                              }
                            />
                            <AlertDialogContent>
                              <AlertDialogHeader>
                                <AlertDialogTitle>
                                  {u.status === "active" ? "停用该用户？" : "启用该用户？"}
                                </AlertDialogTitle>
                                <AlertDialogDescription>
                                  {u.status === "active"
                                    ? `停用后 ${u.email} 的全部登录会话立即失效（需重新启用并重新登录）。`
                                    : `启用后 ${u.email} 可重新登录。`}
                                </AlertDialogDescription>
                              </AlertDialogHeader>
                              <AlertDialogFooter>
                                <AlertDialogCancel>取消</AlertDialogCancel>
                                <AlertDialogCancel
                                  onClick={() =>
                                    void patchUser(u.email, {
                                      status: u.status === "active" ? "disabled" : "active",
                                    })
                                  }
                                >
                                  确认
                                </AlertDialogCancel>
                              </AlertDialogFooter>
                            </AlertDialogContent>
                          </AlertDialog>
                        )}
                      </TableCell>
                      <TableCell>
                        {editingLimit === u.email ? (
                          <span className="flex items-center gap-1">
                            <Input
                              autoFocus
                              value={limitDraft}
                              onChange={(e) => setLimitDraft(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" && !e.nativeEvent.isComposing) void saveLimit(u);
                                if (e.key === "Escape") setEditingLimit(null);
                              }}
                              placeholder="数字或 -"
                              className="h-8 w-24 text-xs tabular-nums"
                              aria-label="个人限额"
                            />
                            <Button variant="ghost" size="icon" className="size-7" onClick={() => void saveLimit(u)} aria-label="保存限额">
                              <Check className="size-3.5" aria-hidden />
                            </Button>
                            <Button variant="ghost" size="icon" className="size-7" onClick={() => setEditingLimit(null)} aria-label="取消">
                              <X className="size-3.5" aria-hidden />
                            </Button>
                          </span>
                        ) : (
                          <button
                            className="text-xs tabular-nums text-muted-foreground underline-offset-2 hover:underline"
                            onClick={() => {
                              setEditingLimit(u.email);
                              setLimitDraft(u.daily_token_limit === null ? "-" : String(u.daily_token_limit));
                            }}
                            title="点击修改个人限额（- 恢复默认 20 万）"
                          >
                            {fmtTokens(u.today_tokens)} /{" "}
                            {u.daily_token_limit === null ? "默认" : fmtTokens(u.daily_token_limit)}
                          </button>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </section>

        {/* 邀请码 */}
        <section aria-label="邀请码" className="rounded-lg border bg-card shadow-sm">
          <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
            <h2 className="text-sm font-semibold">邀请码</h2>
            <span className="text-xs text-muted-foreground">封闭注册的发放凭据</span>
          </div>
          <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
            <Input
              value={inviteUses}
              onChange={(e) => setInviteUses(e.target.value)}
              placeholder="次数"
              className="h-9 w-20 text-xs tabular-nums"
              aria-label="可用次数"
            />
            <Input
              value={inviteDays}
              onChange={(e) => setInviteDays(e.target.value)}
              placeholder="有效天数（默认永久）"
              className="h-9 w-40 text-xs tabular-nums"
              aria-label="有效天数"
            />
            <Input
              value={inviteNote}
              onChange={(e) => setInviteNote(e.target.value)}
              placeholder="备注（发放批次）"
              maxLength={100}
              className="h-9 min-w-40 flex-1 text-xs"
              aria-label="备注"
            />
            <Button onClick={() => void issueInvite()} disabled={inviteBusy} className="h-9 shrink-0 gap-1.5">
              {inviteBusy ? <Loader2 className="animate-spin" aria-hidden /> : <Plus className="size-4" aria-hidden />}
              发放
            </Button>
          </div>
          {lastCode && (
            <p className="border-b px-4 py-2 text-xs">
              最新邀请码：<span className="font-mono font-medium">{lastCode}</span>
            </p>
          )}
          {invites === null ? (
            <p className="px-4 py-4 text-sm text-muted-foreground">加载中…</p>
          ) : invites.length === 0 ? (
            <p className="px-4 py-4 text-sm text-muted-foreground">暂无邀请码</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>码</TableHead>
                  <TableHead className="w-28">核销</TableHead>
                  <TableHead className="w-36">有效期至</TableHead>
                  <TableHead>备注</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {invites.map((i) => {
                  const expired = i.expires_at !== null && new Date(i.expires_at) < new Date();
                  const exhausted = i.used_count >= i.max_uses;
                  return (
                    <TableRow key={i.code}>
                      <TableCell className="font-mono text-xs">{i.code}</TableCell>
                      <TableCell className="text-xs tabular-nums">
                        {i.used_count} / {i.max_uses}
                        {(expired || exhausted) && (
                          <span className="ml-1.5 text-muted-foreground">
                            （{exhausted ? "已用尽" : "已过期"}）
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="text-xs">
                        {i.expires_at ? i.expires_at.slice(0, 10) : "永久"}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {i.note || i.created_by}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </section>

        {/* 会话巡查 */}
        <section aria-label="会话巡查" className="rounded-lg border bg-card shadow-sm">
          <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
            <h2 className="text-sm font-semibold">会话巡查</h2>
            <span className="text-xs text-muted-foreground">列表级巡查（内容级不开放）；删除连带清历史</span>
            <span className="ml-auto flex items-center gap-2">
              <Select value={sessKind} onValueChange={(v) => setSessKind(v ?? "all")}>
                <SelectTrigger className="h-8 w-28 text-xs" aria-label="会话类型过滤">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部类型</SelectItem>
                  <SelectItem value="chat">对话</SelectItem>
                </SelectContent>
              </Select>
              <Input
                value={sessQ}
                onChange={(e) => setSessQ(e.target.value)}
                placeholder="按用户/标题过滤"
                maxLength={64}
                className="h-8 w-44 text-xs"
                aria-label="会话过滤词"
              />
            </span>
          </div>
          {sessions === null ? (
            <p className="px-4 py-4 text-sm text-muted-foreground">加载中…</p>
          ) : sessions.length === 0 ? (
            <p className="px-4 py-4 text-sm text-muted-foreground">无匹配会话</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>用户</TableHead>
                  <TableHead>标题</TableHead>
                  <TableHead className="w-24">类型</TableHead>
                  <TableHead className="w-36">最近活动</TableHead>
                  <TableHead className="w-20 text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sessions.map((s) => (
                  <TableRow key={s.session_id}>
                    <TableCell className="text-xs">{s.user}</TableCell>
                    <TableCell className="max-w-60 truncate text-sm">
                      {s.title || "（未命名）"}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {s.kind === "chat" ? "对话" : "对比"}
                    </TableCell>
                    <TableCell className="text-xs tabular-nums">
                      {s.updated_at.slice(0, 16).replace("T", " ")}
                    </TableCell>
                    <TableCell className="text-right">
                      <AlertDialog>
                        <AlertDialogTrigger
                          render={
                            <Button variant="ghost" size="icon" className="size-7" aria-label="删除会话">
                              <Trash2 className="size-3.5" aria-hidden />
                            </Button>
                          }
                        />
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>删除该会话？</AlertDialogTitle>
                            <AlertDialogDescription>
                              将删除 {s.user} 的「{s.title || "未命名"}」及其对话历史与相关记忆，此操作不可撤销。
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>取消</AlertDialogCancel>
                            <AlertDialogCancel variant="destructive" onClick={() => void removeSession(s.session_id)}>
                              删除
                            </AlertDialogCancel>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </section>

        {/* 用量趋势 */}
        <section aria-label="用量统计" className="rounded-lg border bg-card shadow-sm">
          <div className="flex flex-wrap items-baseline gap-2 border-b px-4 py-3">
            <h2 className="text-sm font-semibold">token 用量</h2>
            <span className="text-xs text-muted-foreground">近 7 日全员合计与今日个人榜</span>
          </div>
          {usage === null ? (
            <p className="px-4 py-4 text-sm text-muted-foreground">加载中…</p>
          ) : (
            <div className="grid gap-0 sm:grid-cols-2 sm:divide-x">
              <div>
                <p className="px-4 pt-3 text-xs text-muted-foreground">近 7 日（全员）</p>
                <Table>
                  <TableBody>
                    {usage.daily.length === 0 && (
                      <TableRow>
                        <TableCell className="text-sm text-muted-foreground">暂无记录</TableCell>
                      </TableRow>
                    )}
                    {usage.daily.map((d) => (
                      <TableRow key={d.day}>
                        <TableCell className="text-xs tabular-nums">{d.day}</TableCell>
                        <TableCell className="text-right text-xs tabular-nums">
                          {fmtTokens(d.tokens)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <div>
                <p className="px-4 pt-3 text-xs text-muted-foreground">今日个人（top 10）</p>
                <Table>
                  <TableBody>
                    {usage.today_top.length === 0 && (
                      <TableRow>
                        <TableCell className="text-sm text-muted-foreground">暂无记录</TableCell>
                      </TableRow>
                    )}
                    {usage.today_top.map((t) => (
                      <TableRow key={t.user}>
                        <TableCell className="text-xs">{t.user}</TableCell>
                        <TableCell className="text-right text-xs tabular-nums">
                          {fmtTokens(t.tokens)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>
          )}
        </section>

        <footer className="pb-4 text-center text-[11px] text-muted-foreground">
          管理操作仅记录于服务日志 · 演示数据为虚构「钱塘大学」合成数据
        </footer>
      </div>
    </main>
  );
}
