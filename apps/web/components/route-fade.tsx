"use client";

import { usePathname } from "next/navigation";

/**
 * P37 路由转场：按 pathname 重挂载内容层，让每次导航都有一帧轻微上浮淡入。
 *
 * 为什么不用 View Transitions 做路由：Next App Router 的路由更新是异步的，
 * 直接包 `startViewTransition` 会在快照之后才渲染新页面（先闪一帧旧内容）。
 * 框架级集成要开 React 实验特性，成本与风险不成比例——所以路由这层只用
 * 一次廉价的入场动画，把「墨染」留在主题切换上（那里是同步状态变更，正合适）。
 */
export default function RouteFade({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div key={pathname} className="page-in min-h-0 flex-1">
      {children}
    </div>
  );
}
