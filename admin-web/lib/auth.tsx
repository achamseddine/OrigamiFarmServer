"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, clearToken, setToken } from "./api";

interface Me {
  user_id: string;
  email: string;
  display_name: string;
}

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (email: string, displayName?: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  async function refresh() {
    const token = typeof window !== "undefined" ? window.localStorage.getItem("origami_token") : null;
    if (!token) {
      setMe(null);
      setLoading(false);
      return;
    }
    try {
      const result = await apiFetch<Me>("/api/v1/me");
      setMe(result);
    } catch {
      clearToken();
      setMe(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function login(email: string, displayName?: string) {
    const result = await apiFetch<{ access_token: string }>("/api/v1/auth/dev-login", {
      method: "POST",
      body: { email, display_name: displayName },
    });
    setToken(result.access_token);
    await refresh();
    router.push("/dashboard");
  }

  function logout() {
    clearToken();
    setMe(null);
    router.push("/login");
  }

  return <AuthContext.Provider value={{ me, loading, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
