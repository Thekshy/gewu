"use client";

import { useCallback, useEffect, useState } from "react";
import { CircleAlert, RefreshCw, RotateCcw, Search } from "lucide-react";
import CountUp from "@/components/CountUp";
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
import { useRequireMember } from "@/lib/auth";
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
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

// 演示控制台：业务台账（交易真实落库的证据 + 演示重置）/ 检索调试（不经 LLM 直接看
// 混合检索命中）/ 语料列表 / 服务健康与预算。全部只读既有 API，零后端改动。

type TabKey = "evidence" | "runtime";

function Panel({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  // P20：SpotlightCard 退役（radial-spotlight-glow + 裁剪定位子元素两项基线），面板回归素 Card
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{title}</CardTitle>
        {note && <CardDescription>{note}</CardDescription>}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
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

function Ledger({ admin }: { admin: boolean }) {
  const [data, setData] = useState<BusinessOverview | null>(null);
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    fetchBusinessOverview(admin) // P21：本人视图；admin 看全量（后端 ?all=1）
      .then(setData)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [admin]);

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
        {data?.scope && (
          <span className="text-xs text-muted-foreground">
            {data.scope === "all" ? "全部台账（管理员）" : "本人台账"}
          </span>
        )}
        {admin && <AlertDialog>
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
                将删除全部场馆预约与请假单（PG 落库数据），用于演示前重置。仅管理员可执行。此操作不可撤销。
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
        </AlertDialog>}
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
            <p className="text-xs font-medium text-muted-foreground">场馆预约（{data.bookings.length}）</p>
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
            <p className="text-xs font-medium text-muted-foreground">请假单（{data.tickets.length}）</p>
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
                      <TableCell className="tabular-nums">{t.days}</TableCell>
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
        // 面板内列表用 divide-y 发丝线分行（operate.md：卡内不套卡）
        <ol className="divide-y divide-border">
          {hits.map((h, i) => (
            <li key={`${h.doc_id}-${h.seq}`} className="py-2.5 first:pt-0 last:pb-0">
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
  const { user } = useRequireMember(); // P39 起正式成员守卫（游客 403 面）；台账本人视图，admin 全量+重置
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [tab, setTab] = useState<TabKey>("evidence");

  useEffect(() => {
    fetchHealth().then(setHealth);
  }, []);

  // P37 受众拆分：这一页原来把「给人看的证据」和「给开发看的运行指标」平铺在
  // 同一个网格里，于是第一次点进来的人分不清该看什么。按意图分两栏：
  //   证据 = 办理真落库 + 检索命中（求职展示最该被看到的东西）
  //   运行 = 服务健康 + 语料清单（引擎指标与数据口径）
  const tabs: { key: TabKey; label: string; hint: string }[] = [
    { key: "evidence", label: "证据", hint: "办理真实落库 · 检索命中" },
    { key: "runtime", label: "运行", hint: "服务健康 · 语料清单" },
  ];
  const activeIndex = tabs.findIndex((t) => t.key === tab);

  function onTabKeyDown(e: React.KeyboardEvent) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const dir = e.key === "ArrowRight" ? 1 : -1;
    const next = tabs[(activeIndex + dir + tabs.length) % tabs.length];
    setTab(next.key);
  }

  return (
    <main className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-4 py-10">
        <header>
          <h1 className="t-h1">演示控制台</h1>
          <p className="t-lead mt-2.5 max-w-lg text-muted-foreground">
            对话页之外的两类入口：证据给人看，运行给开发看。
          </p>
        </header>

        <div
          role="tablist"
          aria-label="控制台分区"
          onKeyDown={onTabKeyDown}
          className="rule-b mt-8 flex gap-6"
        >
          {tabs.map((t) => {
            const active = t.key === tab;
            return (
              <button
                key={t.key}
                role="tab"
                id={`console-tab-${t.key}`}
                aria-selected={active}
                aria-controls={`console-panel-${t.key}`}
                tabIndex={active ? 0 : -1}
                onClick={() => setTab(t.key)}
                className={
                  "group relative flex items-baseline gap-2 pb-2.5 text-left transition-colors " +
                  (active ? "text-foreground" : "text-muted-foreground hover:text-foreground")
                }
              >
                <span className="t-h3">{t.label}</span>
                <span className="t-meta text-muted-foreground/80">{t.hint}</span>
                {/* 激活态的墨线；hover 时也画出来（落笔） */}
                <span
                  aria-hidden
                  className={
                    "pointer-events-none absolute inset-x-0 -bottom-px h-0.5 origin-left bg-seal transition-transform duration-200 ease-out-expo " +
                    (active ? "scale-x-100" : "scale-x-0 group-hover:scale-x-100")
                  }
                />
              </button>
            );
          })}
        </div>

        <div
          role="tabpanel"
          id={`console-panel-${tab}`}
          aria-labelledby={`console-tab-${tab}`}
          className="mt-6 grid items-start gap-4 md:grid-cols-2"
        >
          {tab === "evidence" ? (
            <>
              <Panel
                title="业务台账"
                note="办理确认后真实落库（PG）；登录者本人视图，管理员可看全部并重置"
              >
                <Ledger admin={user?.role === "admin"} />
              </Panel>
              <Panel title="检索命中" note="直接调 /api/search：BM25 + 向量 RRF 混合命中，不经 LLM">
                <SearchBench />
              </Panel>
            </>
          ) : (
            <>
              <Panel title="服务健康">
                <Health health={health} />
              </Panel>
              <Panel title="语料" note="GET /api/docs：已入库的虚构「钱塘大学」政策文档">
                <Corpus />
              </Panel>
            </>
          )}
        </div>

        <footer className="t-meta pb-4 pt-10 text-center text-muted-foreground/80">
          全部数据来自只读/调试 API · 格物 Gewu 求职展示项目
        </footer>
      </div>
    </main>
  );
}
