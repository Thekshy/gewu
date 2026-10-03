"use client";

import { useEffect, useState } from "react";
import { Check, ChevronRight, Loader2, Pencil, Plus, Trash2, TriangleAlert, X } from "lucide-react";
import {
  deleteFact,
  listFacts,
  upsertFact,
  type Fact,
  type FactKind,
} from "@/lib/api";
import { useRequireUser } from "@/lib/auth";
import { cn } from "@/lib/utils";
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
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

/**
 * 记忆面板（P22 建，P37 改形态）。
 *
 * 用户口径：「记忆主要是我想看看系统的记忆模块做得怎么样」——所以这一页是
 * **一页两读**：
 *   ① 格物记住的事（人读）：值在前、类别居中、内部 key 退成注脚；顺手可改可删。
 *   ② 原始事实表（观察读）：折叠在下面，保留 kind 分组 / 标识 / upsert 语义，
 *      供观察抽取质量与手工维护。
 *
 * 旧形态把 ② 直接当成了默认界面（还要求用户手输 `major` 这种英文 key），
 * 等于把存储 schema 端给了使用者——P37 判词：那是把实现当产品。
 */

const KIND_GROUPS: { kind: FactKind; label: string; hint: string }[] = [
  { kind: "profile", label: "身份", hint: "专业、年级、姓名、宿舍等稳定属性" },
  { kind: "preference", label: "偏好", hint: "运动偏好、场馆习惯等" },
  { kind: "constraint", label: "约束", hint: "预算、时间限制等硬性条件" },
];

const KIND_OPTIONS = KIND_GROUPS.map(({ kind, label }) => ({ value: kind, label }));
const KIND_LABEL: Record<string, string> = Object.fromEntries(
  KIND_GROUPS.map(({ kind, label }) => [kind, label]),
);

export default function MemoryPage() {
  const { user } = useRequireUser();
  const [facts, setFacts] = useState<Fact[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // 行内编辑
  const [editing, setEditing] = useState<string | null>(null); // `${kind}/${key}`
  const [draft, setDraft] = useState("");
  // 新增表单
  const [newKind, setNewKind] = useState<FactKind>("profile");
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!user) return;
    let alive = true;
    listFacts()
      .then((fs) => alive && setFacts(fs))
      .catch((e) => alive && setErr(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [user]);

  async function refresh() {
    try {
      setFacts(await listFacts());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function saveEdit(kind: FactKind, key: string) {
    const value = draft.trim();
    setEditing(null);
    if (!value || value === facts?.find((f) => f.kind === kind && f.key === key)?.value) return;
    try {
      await upsertFact({ kind, key, value });
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function remove(kind: FactKind, key: string) {
    try {
      await deleteFact(kind, key);
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function add() {
    const key = newKey.trim();
    const value = newValue.trim();
    if (!key || !value) return;
    setSaving(true);
    try {
      await upsertFact({ kind: newKind, key, value });
      setNewKey("");
      setNewValue("");
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  const ofKind = (k: FactKind) => (facts ?? []).filter((f) => f.kind === k);

  /** 编辑态输入（人读区与原始表共用；Enter 保存 / Escape 取消）。 */
  function EditInput({ fact }: { fact: Fact }) {
    return (
      <span className="flex items-center gap-1">
        <Input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing) void saveEdit(fact.kind, fact.key);
            if (e.key === "Escape") setEditing(null);
          }}
          maxLength={500}
          className="h-8 text-sm"
          aria-label="事实内容"
        />
        <Button
          variant="ghost"
          size="icon"
          className="size-7 shrink-0"
          onClick={() => void saveEdit(fact.kind, fact.key)}
          aria-label="保存"
        >
          <Check className="size-3.5" aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 shrink-0"
          onClick={() => setEditing(null)}
          aria-label="取消"
        >
          <X className="size-3.5" aria-hidden />
        </Button>
      </span>
    );
  }

  /** 行内操作：编辑 + 删除（删除带二次确认）。两读区共用，避免两份语义。 */
  function RowActions({ fact }: { fact: Fact }) {
    const id = `${fact.kind}/${fact.key}`;
    if (editing === id) return null;
    return (
      <span className="inline-flex shrink-0 items-center gap-0.5">
        <Button
          variant="ghost"
          size="icon"
          className="size-7 text-muted-foreground"
          onClick={() => {
            setEditing(id);
            setDraft(fact.value);
          }}
          aria-label="编辑"
        >
          <Pencil className="size-3.5" aria-hidden />
        </Button>
        <AlertDialog>
          <AlertDialogTrigger
            render={
              <Button variant="ghost" size="icon" className="size-7 text-muted-foreground" aria-label="删除">
                <Trash2 className="size-3.5" aria-hidden />
              </Button>
            }
          />
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>删除这条记忆？</AlertDialogTitle>
              <AlertDialogDescription>
                将删除 {KIND_LABEL[fact.kind] ?? fact.kind}「{fact.value}」，此操作不可撤销。
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>取消</AlertDialogCancel>
              {/* Close 原语：点击关闭弹窗并执行删除 */}
              <AlertDialogCancel variant="destructive" onClick={() => void remove(fact.kind, fact.key)}>
                删除
              </AlertDialogCancel>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </span>
    );
  }

  return (
    <main className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-10">
        <header>
          <h1 className="t-h1">记忆</h1>
          <p className="t-lead mt-2.5 max-w-lg text-muted-foreground">
            事实由对话自动抽取，每轮对话注入上下文。上面是格物记住的事，下面是原始事实表。
          </p>
        </header>

        {err && (
          <Alert variant="destructive" className="mt-7">
            <TriangleAlert className="size-4" aria-hidden />
            <AlertDescription>
              操作失败：{err}
              <button className="ml-2 underline underline-offset-2" onClick={() => setErr(null)}>
                关闭
              </button>
            </AlertDescription>
          </Alert>
        )}

        {facts === null ? (
          <p className="t-small mt-8 flex items-center gap-2 text-muted-foreground" role="status">
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
            正在加载记忆…
          </p>
        ) : (
          <>
            {/* ① 人读：值在前，key 退成注脚 */}
            <section className="mt-9">
              <h2 className="t-meta rule-b flex items-center gap-2 pb-2 font-semibold text-muted-foreground">
                <span className="size-2 bg-seal" aria-hidden />
                格物记住的事
                <span className="t-num text-muted-foreground/70">{facts.length}</span>
              </h2>
              {facts.length === 0 ? (
                <p className="t-small max-w-md py-5 text-muted-foreground">
                  还没有记住任何事。在对话里说「记住我每周三下午没课，帮我约场馆避开这个时间」，
                  它就会开始记。
                </p>
              ) : (
                <ul className="divide-y divide-border/70">
                  {facts.map((f) => {
                    const id = `${f.kind}/${f.key}`;
                    return (
                      <li key={id} className="flex items-center gap-3 py-3.5">
                        <span className="min-w-0 flex-1">
                          {editing === id ? (
                            <EditInput fact={f} />
                          ) : (
                            <span className="t-h3 block break-words">{f.value}</span>
                          )}
                        </span>
                        <span className="t-meta shrink-0 rounded-sm bg-accent px-1.5 py-0.5 text-accent-foreground">
                          {KIND_LABEL[f.kind] ?? f.kind}
                        </span>
                        <span className="t-num hidden shrink-0 text-[0.625rem] text-muted-foreground/60 sm:inline">
                          {f.key}
                        </span>
                        <RowActions fact={f} />
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>

            {/* ② 观察读：折叠的原始事实表（类别分组 / 标识 / upsert） */}
            <Collapsible className="mt-10">
              <CollapsibleTrigger className="group/trace t-small flex items-center gap-1.5 font-medium text-muted-foreground transition-colors hover:text-foreground">
                <ChevronRight
                  className="size-3.5 transition-transform group-data-[panel-open]/trace:rotate-90"
                  aria-hidden
                />
                原始事实表 · 观察与手工维护
              </CollapsibleTrigger>
              <CollapsibleContent>
                <div className="mt-4 space-y-5">
                  {KIND_GROUPS.map(({ kind, label, hint }) => {
                    const rows = ofKind(kind);
                    return (
                      <section key={kind} aria-label={label}>
                        <div className="rule-b flex flex-wrap items-baseline gap-2 pb-2">
                          <h3 className="t-h3">{label}事实</h3>
                          <span className="t-meta text-muted-foreground">{hint}</span>
                          <span className="t-num ml-auto text-muted-foreground">{rows.length} 条</span>
                        </div>
                        {rows.length === 0 ? (
                          <p className="t-small py-3 text-muted-foreground">暂无记录</p>
                        ) : (
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead className="w-40">标识</TableHead>
                                <TableHead>内容</TableHead>
                                <TableHead className="w-24 text-right">操作</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {rows.map((f) => {
                                const id = `${f.kind}/${f.key}`;
                                return (
                                  <TableRow key={id}>
                                    <TableCell className="t-num text-xs">{f.key}</TableCell>
                                    <TableCell>
                                      {editing === id ? (
                                        <EditInput fact={f} />
                                      ) : (
                                        <span className="t-small">{f.value}</span>
                                      )}
                                    </TableCell>
                                    <TableCell className="text-right">
                                      <RowActions fact={f} />
                                    </TableCell>
                                  </TableRow>
                                );
                              })}
                            </TableBody>
                          </Table>
                        )}
                      </section>
                    );
                  })}

                  {/* 新增/覆盖：工程向表单，留在观察区（人读区的新增走对话） */}
                  <section aria-label="新增事实" className="rule-t pt-5">
                    <h3 className="t-h3 mb-3">新增 / 覆盖事实</h3>
                    <div className="flex flex-wrap items-center gap-2">
                      <Select value={newKind} onValueChange={(v) => setNewKind(v as FactKind)}>
                        <SelectTrigger className="h-9 w-31 shrink-0" aria-label="事实类别">
                          <SelectValue>
                            {KIND_OPTIONS.find((o) => o.value === newKind)?.label}
                          </SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                          {KIND_OPTIONS.map((o) => (
                            <SelectItem key={o.value} value={o.value}>
                              {o.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <Input
                        value={newKey}
                        onChange={(e) => setNewKey(e.target.value)}
                        placeholder="标识（英文小写，如 major）"
                        maxLength={60}
                        className="t-num h-9 w-52 shrink-0 text-xs"
                        aria-label="事实标识"
                      />
                      <Input
                        value={newValue}
                        onChange={(e) => setNewValue(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && !e.nativeEvent.isComposing) void add();
                        }}
                        placeholder="内容（如 计算机科学）"
                        maxLength={500}
                        className="h-9 min-w-48 flex-1 text-sm"
                        aria-label="事实内容"
                      />
                      <Button
                        onClick={() => void add()}
                        disabled={saving || !newKey.trim() || !newValue.trim()}
                        className="h-9 shrink-0 gap-1.5"
                      >
                        {saving ? (
                          <Loader2 className="animate-spin" aria-hidden />
                        ) : (
                          <Plus className="size-4" aria-hidden />
                        )}
                        添加
                      </Button>
                    </div>
                    <p className="t-meta mt-2 text-muted-foreground">
                      同类别同标识的事实会被新值覆盖（upsert 语义）。日常增改走对话即可。
                    </p>
                  </section>
                </div>
              </CollapsibleContent>
            </Collapsible>
          </>
        )}

        <footer className={cn("t-meta rule-t mt-12 pt-4 text-muted-foreground/80")}>
          数据来自 memory_fact（本人视图）· 演示语料与业务系统均为虚构的「钱塘大学」合成数据
        </footer>
      </div>
    </main>
  );
}
