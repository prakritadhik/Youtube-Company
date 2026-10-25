"use client";

import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, setCsrf } from "@/lib/api";

export interface AuthState {
  loading: boolean;
  initialized: boolean;
  authenticated: boolean;
  user: { username: string; role: string; permissions: string[] } | null;
  refresh: () => Promise<void>;
  can: (permission: string) => boolean;
}

const Ctx = createContext<AuthState | null>(null);

interface StatusResponse {
  initialized: boolean; authenticated: boolean; csrf_token: string | null;
  user: { username: string; role: string; permissions: string[] } | null;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<Omit<AuthState, "refresh" | "can">>({ loading: true, initialized: true, authenticated: false, user: null });

  const refresh = useCallback(async () => {
    try {
      const s = await api<StatusResponse>("/api/auth/status");
      setCsrf(s.csrf_token);
      setState({ loading: false, initialized: s.initialized, authenticated: s.authenticated, user: s.user });
    } catch {
      setState((p) => ({ ...p, loading: false }));
    }
  }, []);

  useEffect(() => {
    refresh();
    const onExpired = () => refresh();
    window.addEventListener("auth:expired", onExpired);
    return () => window.removeEventListener("auth:expired", onExpired);
  }, [refresh]);

  const can = useCallback((p: string) => !!state.user?.permissions.includes(p), [state.user]);
  return <Ctx.Provider value={{ ...state, refresh, can }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
