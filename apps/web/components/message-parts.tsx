"use client";

import { useState } from "react";
import { motion } from "motion/react";
import {
  BookMarked,
  Check,
  CheckCircle2,
  ChevronRight,
  Copy,
  Loader2,
  ThumbsDown,
  ThumbsUp,
  XCircle,
} from "lucide-react";
import type { ActionResult, Citation, DoneReason, PendingAction, Step } from "@/lib/api";
import { DONE_BADGE, ROUTE_LABEL, SLOT_LABEL } from "@/lib/labels";
import { cn } from "@/lib/utils";
import SourcesDialog from "@/components/sources-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

/** 聊天主页的消息积木：路由徽章 / 研究过程 / 槽位卡 /
 * 确认卡 / 回执 / 引用 / 消息操作条 / 结束元信息。纯展示不含数据流——
 * 反馈上报由页面层经 onFeedback 回调注入（P25）。 */

const ROUTE_VARIANT: Record<string, "default" | "secondary" | "outline" | "destructive"> = {
  factual: "secondary",
  research: "default",
  refusal: "outline",
  transaction: "secondary",
  hybrid: "default",
};

export function RouteBadge({ route, reason }: { route: string; reason?: string }) {
  return (
    <Badge variant={ROUTE_VARIANT[route] ?? "secondary"} title={reason}>
      {ROUTE_LABEL[route] ?? route}
    </Badge>
  );
}

export function StatusLine({ text }: { text: string }) {
  return (
    <p className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="size-3.5 animate-spin" aria-hidden />
      {text}
    </p>
  );
}

/** 等待 LLM 首包的弹跳圆点指示器（比 spinner+文字更有「在思考」的呼吸感）。 */
export function TypingDots({ label = "思考中" }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2.5 text-sm text-muted-foreground">
      <span className="inline-flex items-center gap-1" aria-hidden>
        {[0, 1, 2].map((i) => (
          <motion.span
            key={i}
            className="size-1.5 rounded-full bg-primary/70"
            animate={{ y: [0, -4, 0], opacity: [0.35, 1, 0.35] }}
            transition={{ duration: 0.9, repeat: Infinity, delay: i * 0.15, ease: "easeInOut" }}
          />
        ))}
      </span>
      {label}…
    </span>
  );
}

export function ResearchTrace({ steps, defaultOpen }: { steps: Step[]; defaultOpen: boolean }) {
  if (steps.length === 0) return null;
  return (
    <Collapsible defaultOpen={defaultOpen}>
      <CollapsibleTrigger className="group/trace flex items-center gap-1 rounded-md text-xs font-medium text-muted-foreground transition-colors hover:text-foreground">
        <ChevronRight className="size-3.5 transition-transform group-data-[panel-open]/trace:rotate-90" aria-hidden />
        研究过程 · {steps.length} 个子问题
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ol className="mt-1.5 space-y-1 border-l pl-4 text-sm">
          {steps.map((s) => (
            <li key={s.index} className="relative">
              <span className="absolute top-[0.55em] -left-[21px] size-1.5 rounded-full bg-muted-foreground/40" aria-hidden />
              <span className="font-medium">{s.subquestion}</span>
              {s.sources.length > 0 && (
                <span className="block text-xs text-muted-foreground">↳ {s.sources.join("、")}</span>
              )}
            </li>
          ))}
        </ol>
      </CollapsibleContent>
    </Collapsible>
  );
}

export function SlotCard({ slot }: { slot: string }) {
  return (
    <div
      className="inline-flex items-center gap-2 rounded-lg border border-dashed px-3 py-1.5 text-sm"
      title={`slot_question: ${slot}`}
    >
      <Badge variant="secondary">待补充</Badge>
      {SLOT_LABEL[slot] ?? slot}
    </div>
  );
}

export function ConfirmCard({
  pending,
  confirmLabel = "确认办理",
  onConfirm,
  onCancel,
  disabled,
}: {
  pending: PendingAction;
  confirmLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
  disabled?: boolean;
}) {
  return (
    // 消息流里唯一的高亮块：单层 Card + primary 调 ring（P20 拍平渐变描边套卡，
    // nested-cards 基线清零）；层级靠 ring/阴影，不靠双容器
    <Card className="gap-3 rounded-xl py-4 shadow-sm ring-primary/30">
      <CardHeader>
        <CardTitle className="text-sm">
          待确认 · {pending.label}
          <span className="ml-2 font-mono text-xs font-normal text-muted-foreground">{pending.tool}</span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
          {Object.entries(pending.args).map(([k, v]) => (
            <div key={k} className="col-span-2 grid grid-cols-subgrid">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="break-words font-medium">{v}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
      <CardFooter className="gap-2">
        <Button onClick={onConfirm} disabled={disabled}>{confirmLabel}</Button>
        <Button variant="outline" onClick={onCancel} disabled={disabled}>
          取消
        </Button>
      </CardFooter>
    </Card>
  );
}

export function ReceiptAlert({ result }: { result: ActionResult }) {
  const ok = result.success;
  if (!ok) {
    return (
      <Alert variant="destructive" className="items-center py-2">
        <XCircle className="size-4" aria-hidden />
        <AlertDescription>{result.message}</AlertDescription>
      </Alert>
    );
  }
  // 办理成功时刻（编辑式白名单之三，DESIGN.md receipt-alert）：成功是全流程
  // 情绪峰值——图标 spring 落定 + 凭证号 mono 独立行升格（数字政务「签收章」）；
  // 仍是单层 Alert，不嵌套卡。
  return (
    <Alert className="items-center gap-3 py-2.5">
      <motion.span
        initial={{ scale: 0.4, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: "spring", stiffness: 500, damping: 30 }}
      >
        <CheckCircle2 className="size-4 text-emerald-600 dark:text-emerald-400" aria-hidden />
      </motion.span>
      <div className="min-w-0 flex-1">
        <span className="text-sm text-foreground">{result.message}</span>
        {result.receipt && (
          <span className="mt-0.5 block font-mono text-xs tracking-wide text-primary">
            凭证号 {result.receipt}
          </span>
        )}
      </div>
    </Alert>
  );
}

export function CitationsRow({ citations, withSource }: { citations: Citation[]; withSource?: boolean }) {
  if (citations.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="mr-1 inline-flex items-center gap-1 text-xs text-muted-foreground">
        <BookMarked className="size-3.5" aria-hidden />
        引用来源
      </span>
      {citations.map((c) => (
        <Tooltip key={c.n}>
          <TooltipTrigger render={
            <Badge variant="secondary" className="max-w-72 truncate font-normal">
              [{c.n}] {c.title}{withSource ? ` · ${c.source}` : ""}
            </Badge>
          } />
          <TooltipContent className="font-mono text-xs">{c.doc_id}</TooltipContent>
        </Tooltip>
      ))}
    </div>
  );
}

export function MessageActions({
  citations,
  text,
  feedback,
  onFeedback,
  onlyCopy,
}: {
  citations: Citation[];
  text: string;
  feedback?: "good" | "bad" | null;
  onFeedback?: (rating: "good" | "bad") => void;
  onlyCopy?: boolean;
}) {
  /** P25-3 消息操作条（DESIGN.md message-actions）：回答底部的安静尾件——
   * 「来源 N」（开 SourcesDialog）/ 👍 / 👎 / 复制。无底色不套卡；历史恢复的
   * 静态消息 onlyCopy（事件级细节不恢复）。反馈上报走回调（本组件纯展示）。 */
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // 剪贴板不可用（非安全上下文等）：静默，按钮态不变
    }
  }

  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5">
      {!onlyCopy && citations.length > 0 && <SourcesDialog citations={citations} />}
      {!onlyCopy && (
        <>
          <Button
            variant="ghost"
            size="icon"
            aria-label="这个回答有帮助"
            className={
              "size-7 text-muted-foreground" +
              (feedback === "good" ? " text-primary" : " hover:text-foreground")
            }
            disabled={feedback != null}
            onClick={() => onFeedback?.("good")}
          >
            <ThumbsUp className="size-3.5" aria-hidden />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label="这个回答没有帮助"
            className={
              "size-7 text-muted-foreground" +
              (feedback === "bad" ? " text-primary" : " hover:text-foreground")
            }
            disabled={feedback != null}
            onClick={() => onFeedback?.("bad")}
          >
            <ThumbsDown className="size-3.5" aria-hidden />
          </Button>
        </>
      )}
      <Button
        variant="ghost"
        size="sm"
        aria-label="复制回答全文"
        className="h-7 gap-1.5 px-2 text-xs font-normal text-muted-foreground hover:text-foreground"
        onClick={() => void copy()}
      >
        {copied ? (
          <Check className="size-3.5" aria-hidden />
        ) : (
          <Copy className="size-3.5" aria-hidden />
        )}
        {copied ? "已复制" : "复制"}
      </Button>
    </div>
  );
}

export function DoneMeta({
  latency,
  doneReason,
  children,
}: {
  latency?: number;
  doneReason?: DoneReason;
  children?: React.ReactNode;
}) {
  const badge = doneReason ? DONE_BADGE[doneReason] : null;
  return (
    <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      {latency != null && <span className="font-mono">{latency} ms</span>}
      {children}
      {badge && (
        <Badge
          variant={badge.cls === "fail" ? "destructive" : "outline"}
          className={cn(badge.cls === "warn" && "border-amber-500/40 text-amber-700 dark:text-amber-400")}
        >
          {badge.text}
        </Badge>
      )}
    </span>
  );
}
