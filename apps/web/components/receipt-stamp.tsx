"use client";

import { motion, useReducedMotion } from "motion/react";

/**
 * P37「落章」——办理成功时刻的印章动作（DESIGN.md 编辑式时刻白名单之三）。
 *
 * 单独成文件的**唯一原因**是衬线域纪律：这枚印章用 font-display（公章的字
 * 该是衬线），而 design-lint 的衬线白名单是按文件锁的——把它独立出来，
 * 白名单就能精确到这一处，不必给 message-parts.tsx 整文件开口子。
 *
 * 语义即动效：这套墨白方向的本体是「公文 · 印章」，所以全流程情绪峰值用
 * 一枚章落下来的方式演出，而不是撒一层发光粒子。
 */
const EASE_OUT_EXPO = [0.16, 1, 0.3, 1] as const;

export default function ReceiptStamp() {
  const reduce = useReducedMotion();
  return (
    <span aria-hidden className="pointer-events-none relative shrink-0 pl-14">
      {/* 压痕：章落下瞬间向外扩一圈淡环 */}
      <motion.span
        initial={reduce ? false : { opacity: 0.45, scale: 0.82 }}
        animate={{ opacity: 0, scale: 1.5 }}
        transition={{ duration: 0.55, ease: EASE_OUT_EXPO, delay: 0.12 }}
        className="absolute right-3 top-1/2 size-9 -translate-y-1/2 rounded-sm border border-seal/50"
      />
      {/* 章本体：1.9→1 + 去模糊 + 回正到 -8° */}
      <motion.span
        initial={reduce ? false : { opacity: 0, scale: 1.9, rotate: -18, filter: "blur(6px)" }}
        animate={{ opacity: 1, scale: 1, rotate: -8, filter: "blur(0px)" }}
        transition={{ duration: 0.42, ease: EASE_OUT_EXPO, delay: 0.1 }}
        className="absolute right-4 top-1/2 -translate-y-1/2 rounded-sm border-2 border-seal/70 px-1.5 py-0.5 font-display text-[0.6875rem] font-semibold tracking-[0.12em] text-seal/85"
      >
        已办理
      </motion.span>
    </span>
  );
}
