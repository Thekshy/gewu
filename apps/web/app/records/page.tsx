"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, Loader2, TriangleAlert } from "lucide-react";
import { fetchBusinessOverview, type BusinessOverview } from "@/lib/api";
import { useRequireUser } from "@/lib/auth";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

/**
 * P37「我的办理」（学生向）：把原本只存在于「演示控制台 → 业务台账」里的
 * 本人办理结果提成正式入口——预约与请假都是确认后真实落库的，学生应该能
 * 自己核对，而不是去调试页里翻。
 *
 * 数据：GET /api/business/overview（本人视图，后端按登录 email 过滤）。
 * 形态遵循 DESIGN.md 墨白契约：发丝线分行、t-* 字阶、单号走 t-num，
 * 不用 Table 管理台外观、不套 Card。
 */
export default function RecordsPage() {
  const { user } = useRequireUser();
  const [data, setData] = useState<BusinessOverview | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    let alive = true;
    fetchBusinessOverview()
      .then((d) => alive && setData(d))
      .catch((e) => alive && setErr(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [user]);

  const bookings = data?.bookings ?? [];
  const tickets = data?.tickets ?? [];
  const isEmpty = data != null && bookings.length === 0 && tickets.length === 0;

  return (
    <main className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-10">
        <header>
          <h1 className="t-h1">我的办理</h1>
          <p className="t-lead mt-2.5 max-w-lg text-muted-foreground">
            这里是你确认过的办理结果。预约与请假在确认后即写入业务库，单号可用于核对。
          </p>
        </header>

        {err && (
          <Alert variant="destructive" className="mt-7">
            <TriangleAlert className="size-4" aria-hidden />
            <AlertDescription>读取办理记录失败：{err}</AlertDescription>
          </Alert>
        )}

        {!err && data == null && (
          <p className="t-small mt-8 flex items-center gap-2 text-muted-foreground" role="status">
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
            正在读取办理记录…
          </p>
        )}

        {isEmpty && (
          <div className="rule-t mt-8 pt-8">
            <p className="t-h3">还没有办理记录</p>
            <p className="t-small mt-2 max-w-md text-muted-foreground">
              去对话页说一句「帮我预约明天晚上的羽毛球馆」或者「帮我请下周一的病假」，
              确认后就会出现在这里。
            </p>
            <Button className="mt-5" nativeButton={false} render={<Link href="/" />}>
              去对话页办理
              <ArrowRight aria-hidden />
            </Button>
          </div>
        )}

        {bookings.length > 0 && (
          <section className="mt-9">
            <h2 className="t-meta rule-b flex items-center gap-2 pb-2 font-semibold text-muted-foreground">
              <span className="size-2 bg-seal" aria-hidden />
              场馆预约
              <span className="t-num text-muted-foreground/70">{bookings.length}</span>
            </h2>
            <ul className="divide-y divide-border/70">
              {bookings.map((b) => (
                <li key={b.booking_id} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-3.5">
                  <span className="t-h3 min-w-0 flex-1 truncate">{b.venue}</span>
                  <span className="t-small shrink-0 text-muted-foreground">
                    {b.date} · {b.slot}
                  </span>
                  <span className="t-num hidden shrink-0 text-[0.6875rem] text-muted-foreground/70 sm:inline">
                    {b.booking_id}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {tickets.length > 0 && (
          <section className="mt-9">
            <h2 className="t-meta rule-b flex items-center gap-2 pb-2 font-semibold text-muted-foreground">
              <span className="size-2 bg-seal" aria-hidden />
              请假单
              <span className="t-num text-muted-foreground/70">{tickets.length}</span>
            </h2>
            <ul className="divide-y divide-border/70">
              {tickets.map((t) => (
                <li key={t.ticket} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-3.5">
                  <span className="t-h3 shrink-0">{t.leave_type}</span>
                  <span className="t-small min-w-0 flex-1 text-muted-foreground">
                    {t.start} ~ {t.end} · <span className="t-num">{t.days}</span> 天 · 审批：{t.approver}
                  </span>
                  <span className="t-meta shrink-0 rounded-sm bg-accent px-1.5 py-0.5 text-accent-foreground">
                    {t.status}
                  </span>
                  <span className="t-num hidden shrink-0 text-[0.6875rem] text-muted-foreground/70 sm:inline">
                    {t.ticket}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <footer className="t-meta rule-t mt-12 pt-4 text-muted-foreground/80">
          数据来自业务库本人视图，仅显示当前账号的办理记录 · 格物 Gewu 求职展示项目
        </footer>
      </div>
    </main>
  );
}
