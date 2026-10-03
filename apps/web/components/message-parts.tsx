"use client";

import { useState } from "react";
import { motion, useReducedMotion } from "motion/react";

/** P34 起标杆实测的交互曲线（DESIGN.md motion）：120-180ms 档配陡 ease-out。 */
const EASE_OUT_EXPO = [0.16, 1, 0.3, 1] as const;
import {
  Check,
  CheckCircle2,
  ChevronRight,
  Copy,
  ThumbsDown,
  ThumbsUp,
  XCircle,
} from "lucide-react";
import type { ActionResult, Citation, DoneReason, PendingAction, Step } from "@/lib/api";
import { DONE_BADGE, ROUTE_LABEL, SLOT_LABEL } from "@/lib/labels";
import { cn } from "@/lib/utils";
import ReceiptStamp from "@/components/receipt-stamp";
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

/** 聊天主页的消息积木：路由徽章 / 研究过程 / 槽位卡 /
 * 确认卡 / 回执 / 引用 / 消息操作条 / 结束元信息。纯展示不含数据流——
 * 反馈上报由页面层经 onFeedback 回调注入（P25）。 */

export function RouteBadge({ route, reason }: { route: string; reason?: string }) {
  // P37：路由徽章从「灰药丸」降为安静的语义标记（seal 圆点 + 小字）——
  // 药丸形状是模板感的主要来源之一（DESIGN.md 负参照 tell#4/#5）。
  return (
    <span title={reason} className="t-meta font-medium text-muted-foreground">
      {ROUTE_LABEL[route] ?? route}
    </span>
  );
}

export function StatusLine({ text }: { text: string }) {
  // P37 活体轨迹之一：状态行不再转圈，而是一枚呼吸墨点 + 文案切换淡入——
  // 「正在检索知识库…」这类中间态本身是 agent 工作的可见证据，值得有生命感。
  return (
    <p className="t-small flex items-center gap-2 text-muted-foreground" role="status">
      <motion.span
        aria-hidden
        className="size-1.5 shrink-0 rounded-full bg-seal"
        animate={{ scale: [1, 1.5, 1], opacity: [0.5, 1, 0.5] }}
        transition={{ duration: 1.3, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.span
        key={text}
        initial={{ opacity: 0, y: 2 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.22, ease: EASE_OUT_EXPO }}
      >
        {text}
      </motion.span>
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

/** 轨迹步骤列表：逐步点亮的圆点 + 发丝线；进行中最后一条呼吸。 */
function StepList({ steps, live, reduce }: { steps: Step[]; live: boolean; reduce: boolean }) {
  return (
    <ol className="mt-2 space-y-1.5 border-l border-border pl-4">
      {steps.map((s, i) => {
        const isLast = i === steps.length - 1;
        return (
          <motion.li
            key={s.index}
            initial={reduce ? false : { opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.32, ease: EASE_OUT_EXPO, delay: reduce ? 0 : 0.04 }}
            className="relative"
          >
            {live && isLast ? (
              <motion.span
                aria-hidden
                className="absolute top-[0.5em] -left-[21px] size-1.5 rounded-full bg-seal"
                animate={{ scale: [1, 1.5, 1], opacity: [0.5, 1, 0.5] }}
                transition={{ duration: 1.3, repeat: Infinity, ease: "easeInOut" }}
              />
            ) : (
              <motion.span
                aria-hidden
                initial={reduce ? false : { scale: 0.4, backgroundColor: "rgba(0,0,0,0)" }}
                animate={{ scale: 1, backgroundColor: "var(--seal)" }}
                transition={{ duration: 0.3, ease: EASE_OUT_EXPO }}
                className="absolute top-[0.55em] -left-[21px] size-1.5 rounded-full"
              />
            )}
            <span className="t-small font-medium">{s.subquestion}</span>
            {s.sources.length > 0 && (
              <span className="t-meta block text-muted-foreground">↳ {s.sources.join("、")}</span>
            )}
          </motion.li>
        );
      })}
    </ol>
  );
}

export function ResearchTrace({ steps, live }: { steps: Step[]; live: boolean }) {
  const reduce = !!useReducedMotion();
  if (steps.length === 0) return null;
  // 完成态：收拢成一行摘要，需要复核时再展开（信息不丢，平时不占位）
  if (!live) {
    return (
      <Collapsible>
        <CollapsibleTrigger className="group/trace t-meta flex items-center gap-1.5 font-medium text-muted-foreground transition-colors hover:text-foreground">
          <ChevronRight
            className="size-3.5 transition-transform group-data-[panel-open]/trace:rotate-90"
            aria-hidden
          />
          研究过程 · {steps.length} 个子问题
        </CollapsibleTrigger>
        <CollapsibleContent>
          <StepList steps={steps} live={false} reduce={reduce} />
        </CollapsibleContent>
      </Collapsible>
    );
  }
  // 进行中：轨迹常开、逐步点亮——这是 gewu 独有的「看得见的 agent」
  return (
    <div className="rule-t pt-2.5">
      <p className="t-meta flex items-center gap-2 font-semibold text-muted-foreground">
        <motion.span
          aria-hidden
          className="size-1.5 rounded-full bg-seal"
          animate={{ scale: [1, 1.5, 1], opacity: [0.5, 1, 0.5] }}
          transition={{ duration: 1.3, repeat: Infinity, ease: "easeInOut" }}
        />
        正在研究
      </p>
      <StepList steps={steps} live reduce={reduce} />
    </div>
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
  const reduce = useReducedMotion();
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
    <Alert className="relative items-center gap-3 overflow-hidden py-2.5">
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
          <motion.span
            initial={reduce ? false : { opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.34, ease: EASE_OUT_EXPO, delay: 0.26 }}
            className="t-num mt-0.5 block text-xs tracking-wide text-primary"
          >
            凭证号 {result.receipt}
          </motion.span>
        )}
      </div>
      {result.receipt && <ReceiptStamp />}
    </Alert>
  );
}

export function CitationsRow({ citations, withSource }: { citations: Citation[]; withSource?: boolean }) {
  const reduce = useReducedMotion(); // 必须在提前返回之前调用（hooks 规则）
  if (citations.length === 0) return null;
  // P37：引用从「一排灰药丸」升格为溯源块——编号用强调色、条目用发丝线
  // 分行、发文部门与 doc_id 各占一列。这是 gewu 真正独有的内容件，
  // 不该是三行小灰块（P34 判词：内容驱动是主战场）。
  return (
    <div className="rule-t mt-1.5 pt-3">
      <div className="mb-1.5 flex items-baseline gap-2">
        <span className="t-meta font-semibold text-muted-foreground">出处</span>
        <span className="t-num text-[0.6875rem] text-muted-foreground/70">
          {citations.length}
        </span>
      </div>
      <ol>
        {citations.map((c, i) => (
          // P37「溯源逐条落定」：答案给出后，出处一行一行落定，分隔线从左画出——
          // 让「引用可溯源」这个主张有自己的节奏，而不是一次性铺满。
          <motion.li
            key={c.n}
            initial={reduce ? false : { opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.36, ease: EASE_OUT_EXPO, delay: i * 0.07 }}
            className="relative flex items-baseline gap-2.5 py-1.5"
          >
            <span className="t-num shrink-0 text-[0.6875rem] font-semibold text-seal">[{c.n}]</span>
            <span className="t-small min-w-0 flex-1 text-foreground">{c.title}</span>
            {withSource && (
              <span className="t-meta hidden shrink-0 text-muted-foreground sm:inline">{c.source}</span>
            )}
            <span className="t-num hidden shrink-0 text-[0.625rem] text-muted-foreground/60 lg:inline">
              {c.doc_id}
            </span>
            <motion.span
              aria-hidden
              initial={reduce ? false : { scaleX: 0 }}
              animate={{ scaleX: 1 }}
              transition={{ duration: 0.42, ease: EASE_OUT_EXPO, delay: i * 0.07 }}
              className="absolute inset-x-0 bottom-0 h-px origin-left bg-border"
            />
          </motion.li>
        ))}
      </ol>
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
