"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

/** 亮暗主题切换（next-themes class 策略）。 */
export default function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  // SSR/CSR 首帧图标不一致会闪：挂载后再按 resolvedTheme 渲染
  useEffect(() => setMounted(true), []);

  return (
    <Button
      variant="ghost"
      size="icon-sm"
      className="text-muted-foreground"
      aria-label="切换亮暗主题"
      onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
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
