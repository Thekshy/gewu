"use client";

import { BookMarked, ChevronRight, Landmark } from "lucide-react";
import type { Citation } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

/** P25-3 来源分组对话框（DESIGN.md sources-dialog，america.gov View sources 同款）：
 * 按 source（发文部门）分组全量展示；组头 = 部门徽标位（图标占位）+ 计数徽章；
 * 组内条目 = [n] 标题（link 蓝下划线观感）+ doc_id mono 平铺（Tooltip 不进 Dialog）。
 * 纯展示组件；引用行 chips（CitationsRow）维持现状不动——渐进呈现不丢信息。 */

export default function SourcesDialog({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;

  // 按发文部门分组，保序（首次出现顺序）
  const groups = new Map<string, Citation[]>();
  for (const c of citations) {
    const key = c.source || "未知来源";
    const arr = groups.get(key);
    if (arr) arr.push(c);
    else groups.set(key, [c]);
  }

  // 触发钮预览：首个部门名 + 其余部门数（「教务处 +2」）
  const names = [...groups.keys()];
  const preview = names.length > 1 ? ` ${names[0]} +${names.length - 1}` : names[0] ? ` ${names[0]}` : "";

  return (
    <Dialog>
      <DialogTrigger
        render={
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1.5 px-2 text-xs font-normal text-muted-foreground"
          />
        }
      >
        <BookMarked className="size-3.5" aria-hidden />
        来源 {citations.length}
        {preview && <span className="text-muted-foreground/70">{preview}</span>}
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            回答来源
            <Badge variant="secondary" className="ml-2 font-normal">
              {citations.length} 条
            </Badge>
          </DialogTitle>
        </DialogHeader>
        <div className="max-h-[60vh] space-y-0.5 overflow-y-auto">
          {[...groups.entries()].map(([source, items], gi) => (
            <Collapsible key={source} defaultOpen={gi === 0}>
              <CollapsibleTrigger className="group/src group flex w-full items-center gap-2 rounded-md px-1.5 py-1.5 text-sm transition-colors hover:bg-accent/50">
                <span
                  className="flex size-7 shrink-0 items-center justify-center rounded-full bg-card"
                  aria-hidden
                >
                  <Landmark className="size-3.5 text-muted-foreground" />
                </span>
                <span className="font-medium">{source}</span>
                <Badge variant="secondary" className="font-normal">
                  {items.length} 来源
                </Badge>
                <ChevronRight
                  className="ml-auto size-3.5 text-muted-foreground transition-transform group-data-[panel-open]/src:rotate-90"
                  aria-hidden
                />
              </CollapsibleTrigger>
              <CollapsibleContent>
                <ul className="mt-0.5 space-y-0.5 pb-1 pl-2">
                  {items.map((c) => (
                    <li key={`${c.n}-${c.doc_id}`} className="flex items-baseline gap-2 rounded-md px-1.5 py-1 text-sm">
                      <Badge variant="outline" className="shrink-0 font-mono text-[10px] font-normal">
                        [{c.n}]
                      </Badge>
                      <span className="min-w-0 flex-1 text-link underline decoration-link/40 underline-offset-2">
                        {c.title}
                      </span>
                      <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{c.doc_id}</span>
                    </li>
                  ))}
                </ul>
              </CollapsibleContent>
            </Collapsible>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
