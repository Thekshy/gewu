"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

/** 浏览器 View Transitions（Chrome/Edge 支持）；不支持时直接切主题。 */
type ViewTransitionDocument = Document & {
  startViewTransition?: (cb: () => void) => { ready: Promise<void> };
};

/** 亮暗主题切换（next-themes class 策略）。
 *
 *  P37「墨染」：主题切换不再是一次瞬时的 class 翻转，而是新主题从按钮位置
 *  以圆形 clip 铺开——墨汁滴在纸上的观感（View Transitions 原生实现）。
 *  不支持该 API 或用户偏好减弱动效时，退化为直接切换。
 */
export default function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  // SSR/CSR 首帧图标不一致会闪：挂载后再按 resolvedTheme 渲染
  useEffect(() => setMounted(true), []);

  function toggle(e: React.MouseEvent<HTMLButtonElement>) {
    const next = resolvedTheme === "dark" ? "light" : "dark";
    const doc = document as ViewTransitionDocument;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!doc.startViewTransition || reduce) {
      setTheme(next);
      return;
    }
    const rect = e.currentTarget.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const radius = Math.hypot(
      Math.max(x, window.innerWidth - x),
      Math.max(y, window.innerHeight - y),
    );
    const transition = doc.startViewTransition(() => setTheme(next));
    transition.ready
      .then(() => {
        document.documentElement.animate(
          {
            clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`],
          },
          {
            duration: 480,
            easing: "cubic-bezier(0.16, 1, 0.3, 1)",
            pseudoElement: "::view-transition-new(root)",
          },
        );
      })
      .catch(() => {
        // 转场被中断（快速连点等）：主题已切，忽略
      });
  }

  return (
    <Button
      variant="ghost"
      size="icon-sm"
      className="text-muted-foreground"
      aria-label="切换亮暗主题"
      onClick={toggle}
    >
      {mounted ? (
        resolvedTheme === "dark" ? (
          <Sun />
        ) : (
          <Moon />
        )
      ) : (
        <Moon className="invisible" />
      )}
    </Button>
  );
}
