"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AUTH_CHANGED_EVENT, fetchMe, logout as apiLogout, type User } from "@/lib/api";

/**
 * 登录态：null=未登录（含加载完成后的判定）；error=me 请求失败（429/5xx/网络），
 * 与未登录严格区分——守卫不得把临时失败当未登录踢去 /login。
 * 监听 AUTH_CHANGED_EVENT：login/register 成功后各实例（含 layout 里不 remount 的 Nav）立即重拉。
 */
export function useUser() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    const refetch = () => {
      fetchMe().then(
        (u) => {
          if (alive) {
            setUser(u);
            setError(false);
            setLoading(false);
          }
        },
        () => {
          if (alive) {
            setError(true);
            setLoading(false);
          }
        },
      );
    };
    refetch();
    window.addEventListener(AUTH_CHANGED_EVENT, refetch);
    return () => {
      alive = false;
      window.removeEventListener(AUTH_CHANGED_EVENT, refetch);
    };
  }, []);

  return { user, loading, error };
}

/** 页面守卫：确认未登录才跳 /login（前端体验层；后端 401 是真正的安全边界）。 */
export function useRequireUser() {
  const { user, loading, error } = useUser();
  const router = useRouter();
  useEffect(() => {
    if (!loading && !error && !user) router.replace("/login");
  }, [loading, error, user, router]);
  return { user, loading };
}

/** 管理页守卫（P23）：未登录跳 /login，非 admin 跳回对话页（后端 403 是真边界）。 */
export function useRequireAdmin() {
  const { user, loading, error } = useUser();
  const router = useRouter();
  useEffect(() => {
    if (loading || error) return;
    if (!user) router.replace("/login");
    else if (user.role !== "admin") router.replace("/");
  }, [loading, error, user, router]);
  return { user, loading };
}

export async function logoutAndRedirect() {
  await apiLogout();
  window.location.href = "/login";
}
