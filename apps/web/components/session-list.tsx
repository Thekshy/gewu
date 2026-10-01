"use client";

import { useState } from "react";
import { Check, MessageSquarePlus, Pencil, Trash2, X } from "lucide-react";
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
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { SessionInfo } from "@/lib/api";

/** 会话侧栏（P22）：导航 chrome——sans、hairline 分隔、无卡片嵌套（DESIGN.md 纪律）。
 *  桌面为左栏常驻，移动端由 page 装进抽屉；本组件只管列表本体与交互回调。 */

interface Props {
  sessions: SessionInfo[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => Promise<void> | void;
  onDelete: (id: string) => Promise<void> | void;
}

export default function SessionList({ sessions, currentId, onSelect, onNew, onRename, onDelete }: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  function startRename(s: SessionInfo) {
    setEditingId(s.session_id);
    setDraft(s.title);
  }

  async function commitRename(id: string) {
    const title = draft.trim();
    setEditingId(null);
    if (title) await onRename(id, title);
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 px-3 py-3">
        <span className="text-xs font-medium text-muted-foreground">会话</span>
        <Button
          variant="ghost"
          size="sm"
          onClick={onNew}
          className="h-7 gap-1.5 px-2 text-xs"
          aria-label="新建对话"
        >
          <MessageSquarePlus className="size-3.5" aria-hidden />
          新对话
        </Button>
      </div>
      <nav aria-label="会话列表" className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {sessions.length === 0 && (
          <p className="px-2 py-4 text-xs text-muted-foreground">暂无历史会话</p>
        )}
        <ul className="space-y-0.5">
          {sessions.map((s) => {
            const active = s.session_id === currentId;
            if (editingId === s.session_id) {
              return (
                <li key={s.session_id} className="flex items-center gap-1 px-1 py-1">
                  <Input
                    autoFocus
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.nativeEvent.isComposing) void commitRename(s.session_id);
                      if (e.key === "Escape") setEditingId(null);
                    }}
                    maxLength={60}
                    className="h-8 text-sm"
                    aria-label="会话标题"
                  />
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7 shrink-0"
                    onClick={() => void commitRename(s.session_id)}
                    aria-label="保存标题"
                  >
                    <Check className="size-3.5" aria-hidden />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7 shrink-0"
                    onClick={() => setEditingId(null)}
                    aria-label="取消改名"
                  >
                    <X className="size-3.5" aria-hidden />
                  </Button>
                </li>
              );
            }
            return (
              <li key={s.session_id}>
                <div
                  className={
                    "group flex items-center gap-1 rounded-md px-2 py-1.5 transition-colors " +
                    (active
                      ? "bg-surface-strong font-medium"
                      : "hover:bg-muted")
                  }
                >
                  <button
                    onClick={() => onSelect(s.session_id)}
                    className="min-w-0 flex-1 truncate text-left text-sm"
                    aria-current={active ? "true" : undefined}
                    title={s.title || "新对话"}
                  >
                    {s.title || "新对话"}
                  </button>
                  <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-6"
                      onClick={() => startRename(s)}
                      aria-label="重命名会话"
                    >
                      <Pencil className="size-3" aria-hidden />
                    </Button>
                    <AlertDialog>
                      <AlertDialogTrigger
                        render={
                          <Button variant="ghost" size="icon" className="size-6" aria-label="删除会话">
                            <Trash2 className="size-3" aria-hidden />
                          </Button>
                        }
                      />
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>删除会话？</AlertDialogTitle>
                          <AlertDialogDescription>
                            「{s.title || "新对话"}」的对话历史与相关记忆将被清除，此操作不可撤销。
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>取消</AlertDialogCancel>
                          {/* Close 原语：点击关闭弹窗并执行删除 */}
                          <AlertDialogCancel variant="destructive" onClick={() => void onDelete(s.session_id)}>
                            删除
                          </AlertDialogCancel>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}
