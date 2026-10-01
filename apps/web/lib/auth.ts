"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchMe, logout as apiLogout, type User } from "@/lib/api";

/** 登录态：null=未登录（含加载完成后的判定）。轻量独立请求，不做全局 Provider。 */
export function useUser() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    fetchMe().then((u) => {
      if (alive) {
        setUser(u);
        setLoading(false);
      }
    });
    return () => {
      alive = false;
    };
  }, []);

  return { user, loading };
}

/** 页面守卫：未登录跳 /login（前端体验层；后端 401 是真正的安全边界）。 */
export function useRequireUser() {
  const { user, loading } = useUser();
  const router = useRouter();
  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);
  return { user, loading };
}

export async function logoutAndRedirect() {
  await apiLogout();
  window.location.href = "/login";
}
