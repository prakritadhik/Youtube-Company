"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

let csrfToken: string | null = null;
export const setCsrf = (t: string | null) => { csrfToken = t; };

export async function api<T = unknown>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const method = init.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const res = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    cache: "no-store",
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  let data: unknown = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    const msg = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d?.msg ?? "").join("; ") : `Request failed (${res.status})`;
    if (res.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new Event("auth:expired"));
    throw new ApiError(res.status, msg, data);
  }
  return data as T;
}

export function useApi<T>(path: string | null, opts: { refreshMs?: number } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const alive = useRef(true);

  const load = useCallback(async (silent = false) => {
    if (path === null) return;
    if (!silent) setLoading(true);
    try {
      const d = await api<T>(path);
      if (alive.current) { setData(d); setError(null); }
    } catch (e) {
      if (alive.current) setError(e as ApiError);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    setData(null);
    load();
    const t = opts.refreshMs ? setInterval(() => load(true), opts.refreshMs) : undefined;
    return () => { alive.current = false; if (t) clearInterval(t); };
  }, [load, opts.refreshMs]);

  return { data, error, loading, reload: () => load(true) };
}
