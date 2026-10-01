"use client";

import { useCallback, useEffect, useState } from "react";
import { CircleAlert, RefreshCw, RotateCcw, Search } from "lucide-react";
import CountUp from "@/components/CountUp";
import SpotlightCard from "@/components/SpotlightCard";
import {
  API_BASE,
  businessReset,
  fetchBusinessOverview,
  fetchDocs,
  fetchHealth,
  search,
  type BusinessOverview,
  type DocInfo,
  type HealthInfo,
  type SearchHit,
} from "@/lib/api";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

// 演示控制台：业务台账（交易真实落库的证据 + 演示重置）/ 检索调试（不经 LLM 直接看
// 混合检索命中）/ 语料列表 / 服务健康与预算。全部只读既有 API，零后端改动。

function Panel({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <SpotlightCard>
      <CardHeader>
        <CardTitle className="text-sm">{title}</CardTitle>
        {note && <CardDescription>{note}</CardDescription>}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </SpotlightCard>
  );
}

function Health({ health }: { health: HealthInfo | null }) {
  if (!health) return <p className="text-sm text-muted-foreground">连不上后端（{API_BASE}）</p>;
  const pct = health.budget.limit > 0 ? Math.min(100, (health.budget.used / health.budget.limit) * 100) : 0;
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
        <span className="inline-flex items-center gap-1.5">
          <span
            className={
              "size-2 rounded-full " +
              (health.llm ? "bg-emerald-500" : "bg-muted-foreground/40")
            }
            aria-hidden
          />
          LLM {health.llm ? "已配置" : "未配置（检索演示模式）"}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span
            className={
              "size-2 rounded-full " +
              (health.embeddings ? "bg-emerald-500" : "bg-muted-foreground/40")
            }
            aria-hidden
          />
          向量 {health.embeddings ? "启用" : "关闭"}
        </span>
        <Badge variant="outline" className="font-mono text-xs font-normal">
          v{health.version}
        </Badge>
      </div>
      <p className="text-muted-foreground">
        语料 <CountUp to={health.docs} duration={1} className="font-semibold text-primary" /> 篇 /{" "}
        <CountUp to={health.chunks} duration={1} className="font-semibold text-primary" /> chunks
      </p>
      <div className="space-y-1.5">
        <p className="text-xs text-muted-foreground">
          今日 token 预算{" "}
          <CountUp to={health.budget.used} duration={1.5} separator="," className="font-semibold text-primary" /> /{" "}
          {health.budget.limit.toLocaleString()}
          <span className="ml-2 tabular-nums">{pct.toFixed(1)}%</span>
        </p>
        <Progress value={pct} className="max-w-sm [&_[data-slot=progress-indicator]]:bg-primary" />
      </div>
    </div>
  );
}

function Ledger() {
  const [data, setData] = useState<BusinessOverview | null>(null);
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    fetchBusinessOverview()
      .then(setData)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(load, [load]);

  async function reset() {
    try {
      await businessReset();
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <>
      <div className="mb-3 flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw aria-hidden />
          刷新
        </Button>
        <AlertDialog>
          <AlertDialogTrigger render={
            <Button variant="outline" size="sm">
              <RotateCcw aria-hidden />
              演示重置
            </Button>
          } />
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>清空业务数据？</AlertDialogTitle>
              <AlertDialogDescription>
                将删除全部场馆预约与请假单（SQLite business.db 落库数据），用于演示前重置。此操作不可撤销。
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>取消</AlertDialogCancel>
              {/* Close 原语：点击关闭弹窗并执行 reset */}
              <AlertDialogCancel variant="destructive" onClick={() => void reset()}>
                确认清空
              </AlertDialogCancel>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
      {err && (
        <Alert variant="destructive" className="mb-3 py-2.5">
          <CircleAlert className="size-4" aria-hidden />
          <AlertDescription>出错了：{err}</AlertDescription>
        </Alert>
      )}
      {data && (
        <div className="space-y-4">
          <div className="space-y-1.5">
            <h3 className="text-xs font-medium text-muted-foreground">场馆预约（{data.bookings.length}）</h3>
            {data.bookings.length === 0 ? (
              <p className="text-sm text-muted-foreground">暂无预约</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>单号</TableHead>
                    <TableHead>场馆</TableHead>
                    <TableHead>日期</TableHead>
                    <TableHead>时段</TableHead>
                    <TableHead>用户</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.bookings.map((bk) => (
                    <TableRow key={bk.booking_id}>
                      <TableCell className="font-mono text-xs">{bk.booking_id}</TableCell>
                      <TableCell>{bk.venue}</TableCell>
                      <TableCell>{bk.date}</TableCell>
                      <TableCell>{bk.slot}</TableCell>
                      <TableCell>{bk.user}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
          <div className="space-y-1.5">
            <h3 className="text-xs font-medium text-muted-foreground">请假单（{data.tickets.length}）</h3>
            {data.tickets.length === 0 ? (
              <p className="text-sm text-muted-foreground">暂无请假单</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>单号</TableHead>
                    <TableHead>类型</TableHead>
                    <TableHead>起止</TableHead>
                    <TableHead>天数</TableHead>
                    <TableHead>审批</TableHead>
                    <TableHead>状态</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.tickets.map((t) => (
                    <TableRow key={t.ticket}>
                      <TableCell className="font-mono text-xs">{t.ticket}</TableCell>
                      <TableCell>{t.leave_type}</TableCell>
                      <TableCell>
                        {t.start} ~ {t.end}
                      </TableCell>
                      <TableCell>{t.days}</TableCell>
                      <TableCell>{t.approver}</TableCell>
                      <TableCell>{t.status}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        </div>
      )}
    </>
  );
}

const K_OPTIONS = [3, 5, 8, 10];

function SearchBench() {
  const [query, setQuery] = useState("转专业绩点要求");
  const [k, setK] = useState(5);
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function go() {
    if (!query.trim() || busy) return;
    setBusy(true);
    setErr("");
    try {
      setHits(await search(query.trim(), k));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setHits(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="mb-3 flex items-center gap-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void go()}
          placeholder="检索词（1~200 字）"
          aria-label="检索词"
          className="h-9 flex-1"
        />
        <Select value={String(k)} onValueChange={(v) => setK(Number(v))}>
          <SelectTrigger className="h-9 w-22 shrink-0" aria-label="top-k">
            <SelectValue>top {k}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {K_OPTIONS.map((n) => (
              <SelectItem key={n} value={String(n)}>
                top {n}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button size="sm" onClick={() => void go()} disabled={busy || !query.trim()}>
          <Search aria-hidden />
          {busy ? "检索中…" : "检索"}
        </Button>
      </div>
      {err && (
        <Alert variant="destructive" className="mb-3 py-2.5">
          <CircleAlert className="size-4" aria-hidden />
          <AlertDescription>出错了：{err}</AlertDescription>
        </Alert>
      )}
      {hits && (
        <ol className="space-y-2.5">
          {hits.map((h, i) => (
            <li key={`${h.doc_id}-${h.seq}`} className="rounded-xl border bg-card px-3 py-2.5">
              <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
                <span className="font-mono text-xs text-muted-foreground">#{i + 1}</span>
                <span className="font-medium">{h.title}</span>
                <span className="text-xs text-muted-foreground">
                  {h.source} · 块 {h.seq}
                </span>
              </div>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{h.text}</p>
            </li>
          ))}
        </ol>
      )}
    </>
  );
}

function Corpus() {
  const [docs, setDocs] = useState<DocInfo[] | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    fetchDocs()
      .then(setDocs)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  if (err) return <p className="text-sm text-destructive">出错了：{err}</p>;
  if (!docs) return <p className="text-sm text-muted-foreground">加载中…</p>;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>标题</TableHead>
          <TableHead>来源</TableHead>
          <TableHead>更新</TableHead>
          <TableHead className="text-right">chunks</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {docs.map((d) => (
          <TableRow key={d.doc_id}>
            <TableCell className="font-medium">{d.title}</TableCell>
            <TableCell className="text-muted-foreground">{d.source}</TableCell>
            <TableCell className="text-muted-foreground">{d.updated}</TableCell>
            <TableCell className="text-right tabular-nums">{d.chunks}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export default function Console() {
  const [health, setHealth] = useState<HealthInfo | null>(null);
  useEffect(() => {
    fetchHealth().then(setHealth);
  }, []);

  return (
    <main className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-6xl space-y-5 px-4 py-6">
        <header>
          <h1 className="font-display text-xl font-semibold">演示控制台</h1>
          <p className="text-sm text-muted-foreground">
            业务台账 · 检索调试 · 语料 · 服务健康——对话页之外的全部调试入口
          </p>
        </header>

        <div className="grid items-start gap-4 md:grid-cols-2">
          <Panel title="服务健康">
            <Health health={health} />
          </Panel>
          <Panel title="业务台账" note="办理确认后真实落库（SQLite business.db）；演示前可一键重置">
            <Ledger />
          </Panel>
          <Panel title="检索调试" note="直接调 /api/search：BM25 + 向量 RRF 混合命中，不经 LLM">
            <SearchBench />
          </Panel>
          <Panel title="语料" note="GET /api/docs：已入库的虚构「钱塘大学」政策文档">
            <Corpus />
          </Panel>
        </div>

        <footer className="pb-4 text-center text-[11px] text-muted-foreground">
          全部数据来自只读/调试 API · 格物 Gewu 求职展示项目
        </footer>
      </div>
    </main>
  );
}
