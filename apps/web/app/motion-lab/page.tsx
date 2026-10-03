"use client";

import { useState } from "react";
import { motion } from "motion/react";
import { CitationsRow, ReceiptAlert } from "@/components/message-parts";
import type { Citation } from "@/lib/api";
import { Button } from "@/components/ui/button";

/**
 * P37 动效评审页（**临时件**，评审完删或按需留作 motion spec）。
 *
 * 为什么需要它：`make design-review` 只能出静态截图，而**动效是时间维度上的
 * 设计**——截图天然评审不了。这一页把真实组件挂上去、可反复重播，配上
 * `Page.startScreencast` 就能录成 GIF 做评审留档。
 *
 * 这不是产品页面：不挂导航、不参与产品叙事。
 */

const CITATIONS: Citation[] = [
  { n: 1, title: "钱塘大学本科生请假与考勤管理规定", source: "学生工作部", doc_id: "0010-leave" },
  { n: 2, title: "钱塘大学本科学业预警与退学处理实施细则", source: "教务处", doc_id: "0014-academic-warning" },
];

const RECEIPT = {
  success: true,
  tool: "book_venue",
  message: "已预约 羽毛球馆 2026-10-04 19:00-21:00",
  receipt: "VE-0006",
};

export default function MotionLabPage() {
  const [stampKey, setStampKey] = useState(0);
  const [citeKey, setCiteKey] = useState(0);
  const [dotKey, setDotKey] = useState(0);

  return (
    <main className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-10">
        <header>
          <h1 className="t-h1">动效评审</h1>
          <p className="t-lead mt-2.5 max-w-lg text-muted-foreground">
            临时页：把真实组件挂在这里反复重播，用于录制与逐帧评审。不挂导航、不属于产品。
          </p>
        </header>

        <section className="mt-9" data-motion="stamp">
          <div className="rule-b flex items-center gap-3 pb-2">
            <h2 className="t-h3">落章</h2>
            <span className="t-meta text-muted-foreground">办理成功时刻 · 420ms expo-out</span>
            <Button size="sm" variant="outline" className="ml-auto" onClick={() => setStampKey((k) => k + 1)}>
              重播
            </Button>
          </div>
          <div className="pt-4" key={stampKey}>
            <ReceiptAlert result={{ ...RECEIPT, receipt: `VE-000${(stampKey % 9) + 1}` }} />
          </div>
        </section>

        <section className="mt-9" data-motion="citations">
          <div className="rule-b flex items-center gap-3 pb-2">
            <h2 className="t-h3">溯源逐条落定</h2>
            <span className="t-meta text-muted-foreground">答案完成后 · 70ms 错峰 + 发丝线画出</span>
            <Button size="sm" variant="outline" className="ml-auto" onClick={() => setCiteKey((k) => k + 1)}>
              重播
            </Button>
          </div>
          <div className="pt-4" key={citeKey}>
            <CitationsRow citations={CITATIONS} withSource />
          </div>
        </section>

        <section className="mt-9" data-motion="dot">
          <div className="rule-b flex items-center gap-3 pb-2">
            <h2 className="t-h3">墨点呼吸</h2>
            <span className="t-meta text-muted-foreground">生成中 1.4s 循环 / 完成即落定</span>
            <Button size="sm" variant="outline" className="ml-auto" onClick={() => setDotKey((k) => k + 1)}>
              重播
            </Button>
          </div>
          {/* 与 app/page.tsx 页边标记同一动效（那里内联、未导出，此处为镜像） */}
          <div className="flex items-center gap-4 pt-4" key={dotKey}>
            <span className="t-meta text-muted-foreground">生成中</span>
            <motion.span
              className="size-1.5 rounded-full bg-seal"
              animate={{ scale: [1, 1.45, 1], opacity: [0.55, 1, 0.55] }}
              transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
            />
            <span className="t-meta text-muted-foreground">完成</span>
            <motion.span
              className="size-1.5 rounded-full bg-seal"
              initial={{ scale: 1.6 }}
              animate={{ scale: 1 }}
              transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            />
          </div>
        </section>

        <footer className="t-meta rule-t mt-12 pt-4 text-muted-foreground/80">
          临时评审页 · 录屏用 `Page.startScreencast` + /tmp/gewu-shots/gif.py
        </footer>
      </div>
    </main>
  );
}
