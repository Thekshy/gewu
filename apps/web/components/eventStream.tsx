"use client";

import { ArrowRight, CircleAlert, Loader2, Search, SquarePen, Wrench } from "lucide-react";
import type { ActionResult, Citation, DoneReason, PendingAction } from "@/lib/api";
import Answer from "@/components/answer";
import {
  CitationsRow,
  ConfirmCard,
  DoneMeta,
  ReceiptAlert,
  RouteBadge,
  StatusLine,
} from "@/components/message-parts";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/** 时间线条目：status / step / slot_question / action_result 的顺序化呈现。 */
export interface TimelineItem {
  kind: "status" | "step" | "slot" | "result";
  text: string;
  detail?: string;
}

/** 一轨一轮的完整状态（compare 页维护，本组件只读渲染）。 */
export interface TrackRound {
  question: string;
  route?: string;
  reason?: string;
  timeline: TimelineItem[];
  answer: string;
  citations: Citation[];
  pendingAction?: PendingAction;
  actionResult?: ActionResult;
  error?: string;
  done: boolean;
  latency?: number;
  doneReason?: DoneReason;
  eventCount: number;
}

const KIND_ICON = {
  status: Loader2,
  step: Search,
  slot: SquarePen,
  result: ArrowRight,
} as const;

/** ReAct 引擎的工具调用只经 status 文本（`调用工具 X…`）可见，渲染为工具 chip。 */
const TOOL_RE = /^调用工具 (\S+?)…$/;

function TimelineRow({ item }: { item: TimelineItem }) {
  const tool = item.kind === "status" ? item.text.match(TOOL_RE)?.[1] : undefined;
  const Icon = KIND_ICON[item.kind];
  return (
    <li className="flex items-start gap-2 text-sm">
      <Icon
        className={
          "mt-1 size-3.5 shrink-0 text-muted-foreground " +
          (item.kind === "status" && !tool ? "animate-spin" : "")
        }
        aria-hidden
      />
      {tool ? (
        <Badge variant="outline" className="gap-1 py-0 font-mono text-xs font-normal">
          <Wrench className="size-3" aria-hidden />
          {tool}
        </Badge>
      ) : (
        <span className="min-w-0 break-words">{item.text}</span>
      )}
      {item.detail && (
        <span className="ml-auto hidden shrink-0 text-xs text-muted-foreground sm:inline">
          {item.detail}
        </span>
      )}
    </li>
  );
}

/**
 * 对比实验台的单轨面板：标题 + 路由判定 + 事件时间线 + 答案 + 引用 + 确认流 + 结束元信息。
 */
export default function TrackPanel({
  tag,
  title,
  note,
  round,
  busy,
  onConfirm,
  onCancel,
}: {
  tag: string;
  title: string;
  note?: string;
  round?: TrackRound;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Card className="gap-4" aria-label={title}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <Badge variant={tag === "A" ? "default" : "secondary"}>{tag}</Badge>
          {title}
          {round?.route && (
            <span className="ml-auto">
              <RouteBadge route={round.route} reason={round.reason} />
            </span>
          )}
        </CardTitle>
        {note && <p className="text-xs leading-relaxed text-muted-foreground">{note}</p>}
      </CardHeader>
      <CardContent className="space-y-3">
        {!round && !busy && <p className="text-sm text-muted-foreground">等待提问…</p>}
        {round && (
          <>
            {/* 用户问题平铺：600 字重 + 「问：」前缀，不用 bg-muted 小块容器（嵌套卡基线清零） */}
            <p className="text-sm leading-relaxed">
              <span className="text-muted-foreground">问：</span>
              <span className="font-semibold">{round.question}</span>
            </p>
            {round.timeline.length > 0 && (
              <ol className="space-y-1.5">
                {round.timeline.map((item, i) => (
                  <TimelineRow key={i} item={item} />
                ))}
              </ol>
            )}
            {!round.done && busy && <StatusLine text="运行中…" />}
            {round.answer && <Answer text={round.answer} />}

            {round.pendingAction && !round.actionResult && round.done && (
              <ConfirmCard
                pending={round.pendingAction}
                confirmLabel="确认办理（本轨）"
                onConfirm={onConfirm}
                onCancel={onCancel}
                disabled={busy}
              />
            )}

            {round.actionResult && <ReceiptAlert result={round.actionResult} />}

            <CitationsRow citations={round.citations} />
            {round.error && (
              <Alert variant="destructive" className="py-2.5">
                <CircleAlert className="size-4" aria-hidden />
                <AlertDescription>出错了：{round.error}</AlertDescription>
              </Alert>
            )}
            {round.done && (
              <DoneMeta latency={round.latency} doneReason={round.doneReason}>
                <span className="font-mono">{round.eventCount} 事件</span>
              </DoneMeta>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
