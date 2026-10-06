"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AUTH_CHANGED_EVENT,
  fetchMe,
  guestSignIn,
  logout as apiLogout,
  type User,
} from "@/lib/api";

/**
 * 登录态：null=未登录（含加载完成后的判定）；error=me 请求失败（429/5xx/网络），
 * 与未登录严格区分——守卫不得把临时失败当未登录踢去 /login。
 * 监听 AUTH_CHANGED_EVENT：login/register/guest 签发成功后各实例（含 layout 里
 * 不 remount 的 Nav）立即重拉。
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

/** P39 游客签发并发去重：同一时刻多守卫实例只发一次（重复签发=多套影子用户行+cookie 互踩）。
 *  只缓存在途 Promise——成功即清缓存，cookie 过期/丢失后的再次进入仍能重新签发。 */
let _guestInFlight: Promise<unknown> | null = null;
function ensureGuest(): Promise<unknown> {
  if (!_guestInFlight) {
    _guestInFlight = guestSignIn().finally(() => {
      _guestInFlight = null;
    });
  }
  return _guestInFlight;
}

/**
 * 页面守卫：确认未登录才跳 /login（前端体验层；后端 401 是真正的安全边界）。
 * P39 autoGuest（缺省开）：未登录先尝试领游客身份——成功即以游客态留在本页
 * （GUEST_MODE 关闭/签发失败 → 回退现状跳 /login）。签发期间 loading 保持 true，
 * 页面骨架不闪跳。
 */
export function useRequireUser({ autoGuest = true }: { autoGuest?: boolean } = {}) {
  const { user, loading, error } = useUser();
  const router = useRouter();
  const [provisioning, setProvisioning] = useState(false);

  useEffect(() => {
    if (loading || error) return;
    if (user) {
      setProvisioning(false);
      return;
    }
    if (!autoGuest) {
      router.replace("/login");
      return;
    }
    let alive = true;
    setProvisioning(true);
    ensureGuest().then(
      () => {
        if (alive) setProvisioning(false); // AUTH_CHANGED_EVENT 已触发 useUser 重拉
      },
      () => {
        if (alive) {
          setProvisioning(false);
          router.replace("/login");
        }
      },
    );
    return () => {
      alive = false;
    };
  }, [loading, error, user, autoGuest, router]);

  return { user, loading: loading || provisioning };
}

/**
 * 正式成员守卫（P39）：未登录或游客 → /login。记忆/控制台等「登录后解锁」的
 * 系统观察面用（游客本身是合法登录态，所以不能只判 user 空）。
 */
export function useRequireMember() {
  const { user, loading, error } = useUser();
  const router = useRouter();
  useEffect(() => {
    if (loading || error) return;
    if (!user || user.role === "guest") router.replace("/login");
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
