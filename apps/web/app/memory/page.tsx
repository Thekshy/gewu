"use client";

import { useEffect, useState } from "react";
import { Check, Loader2, Pencil, Plus, Trash2, TriangleAlert, X } from "lucide-react";
import {
  deleteFact,
  listFacts,
  upsertFact,
  type Fact,
  type FactKind,
} from "@/lib/api";
import { useRequireUser } from "@/lib/auth";
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

// 记忆面板（P22）：memory_fact 从「黑盒增强」变「透明资产」——查看/编辑/删除/新增
// 一页承载。结构按 console 页的 Operate 惯例：素 Card 面板 + 表格 divide-y 分组，
// 不嵌套卡（DESIGN.md kit-规则）。

const KIND_GROUPS: { kind: FactKind; label: string; hint: string }[] = [
  { kind: "profile", label: "身份事实", hint: "专业、年级、姓名、宿舍等稳定属性" },
  { kind: "preference", label: "偏好", hint: "运动偏好、场馆习惯等" },
  { kind: "constraint", label: "约束条件", hint: "预算、时间限制等硬性条件" },
];

const KIND_OPTIONS = KIND_GROUPS.map(({ kind, label }) => ({ value: kind, label }));

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

  return (
    <main className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl space-y-5 px-4 py-6">
        <header className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-semibold">长期记忆</h1>
            <p className="text-sm text-muted-foreground">
              事实由对话中自动抽取，也可手动维护；每轮对话注入上下文
            </p>
          </div>
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

        {facts === null ? (
          <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground" role="status">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            正在加载记忆…
          </div>
        ) : (
          <div className="space-y-5">
            {KIND_GROUPS.map(({ kind, label, hint }) => {
              const rows = ofKind(kind);
              return (
                <section key={kind} aria-label={label} className="rounded-lg border bg-card shadow-sm">
                  <div className="flex flex-wrap items-baseline gap-2 border-b px-4 py-3">
                    <h2 className="text-sm font-semibold">{label}</h2>
                    <span className="text-xs text-muted-foreground">{hint}</span>
                    <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                      {rows.length} 条
                    </span>
                  </div>
                  {rows.length === 0 ? (
                    <p className="px-4 py-4 text-sm text-muted-foreground">暂无记录</p>
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
                          const isEditing = editing === id;
                          return (
                            <TableRow key={id}>
                              <TableCell className="font-mono text-xs">{f.key}</TableCell>
                              <TableCell>
                                {isEditing ? (
                                  <div className="flex items-center gap-1">
                                    <Input
                                      autoFocus
                                      value={draft}
                                      onChange={(e) => setDraft(e.target.value)}
                                      onKeyDown={(e) => {
                                        if (e.key === "Enter" && !e.nativeEvent.isComposing)
                                          void saveEdit(f.kind, f.key);
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
                                      onClick={() => void saveEdit(f.kind, f.key)}
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
                                  </div>
                                ) : (
                                  <span className="text-sm">{f.value}</span>
                                )}
                              </TableCell>
                              <TableCell className="text-right">
                                {!isEditing && (
                                  <span className="inline-flex items-center gap-0.5">
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      className="size-7"
                                      onClick={() => {
                                        setEditing(id);
                                        setDraft(f.value);
                                      }}
                                      aria-label="编辑"
                                    >
                                      <Pencil className="size-3.5" aria-hidden />
                                    </Button>
                                    <AlertDialog>
                                      <AlertDialogTrigger
                                        render={
                                          <Button
                                            variant="ghost"
                                            size="icon"
                                            className="size-7"
                                            aria-label="删除"
                                          >
                                            <Trash2 className="size-3.5" aria-hidden />
                                          </Button>
                                        }
                                      />
                                      <AlertDialogContent>
                                        <AlertDialogHeader>
                                          <AlertDialogTitle>删除这条记忆？</AlertDialogTitle>
                                          <AlertDialogDescription>
                                            将删除 {label}「{f.key}」（{f.value}），此操作不可撤销。
                                          </AlertDialogDescription>
                                        </AlertDialogHeader>
                                        <AlertDialogFooter>
                                          <AlertDialogCancel>取消</AlertDialogCancel>
                                          {/* Close 原语：点击关闭弹窗并执行删除 */}
                                          <AlertDialogCancel
                                            variant="destructive"
                                            onClick={() => void remove(f.kind, f.key)}
                                          >
                                            删除
                                          </AlertDialogCancel>
                                        </AlertDialogFooter>
                                      </AlertDialogContent>
                                    </AlertDialog>
                                  </span>
                                )}
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

            {/* 新增表单：面板内一行式（kind select + key + value + 添加） */}
            <section aria-label="新增事实" className="rounded-lg border bg-card p-4 shadow-sm">
              <h2 className="mb-3 text-sm font-semibold">新增 / 覆盖事实</h2>
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
                  className="h-9 w-52 shrink-0 font-mono text-xs"
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
              <p className="mt-2 text-xs text-muted-foreground">
                同类别同标识的事实会被新值覆盖（upsert 语义）。
              </p>
            </section>
          </div>
        )}
      </div>
    </main>
  );
}
