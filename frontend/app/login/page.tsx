"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/components/auth";
import { Button, ErrorNote, inputCls, Loading } from "@/components/ui";

export default function LoginPage() {
  const { loading, initialized, authenticated, refresh } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("owner");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState<{ message: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (!loading && authenticated) router.replace("/"); }, [loading, authenticated, router]);
  if (loading) return <Loading />;

  const setup = !initialized;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (setup && password !== confirm) { setErr({ message: "The two passwords do not match." }); return; }
    setBusy(true);
    try {
      await api(setup ? "/api/auth/bootstrap" : "/api/auth/login", { method: "POST", body: { username, password } });
      await refresh();
      router.replace("/");
    } catch (ex) {
      setErr(ex as Error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-2xl border border-line bg-surface p-7" aria-labelledby="t">
        <div>
          <h1 id="t" className="text-xl font-semibold tracking-tight">{setup ? "Set up your owner account" : "Sign in"}</h1>
          <p className="mt-1 text-sm text-muted">
            {setup
              ? "First run. This account controls budgets, approvals and publishing. Setup works once and only from this computer."
              : "AI Media Company owner dashboard."}
          </p>
        </div>
        <div>
          <label htmlFor="u" className="mb-1 block text-sm font-medium">Username</label>
          <input id="u" className={inputCls} value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required minLength={3} />
        </div>
        <div>
          <label htmlFor="p" className="mb-1 block text-sm font-medium">Password</label>
          <input id="p" type="password" className={inputCls} value={password} onChange={(e) => setPassword(e.target.value)}
            autoComplete={setup ? "new-password" : "current-password"} required />
          {setup && <p className="mt-1 text-xs text-muted">At least 12 characters, with upper and lower case letters and a digit.</p>}
        </div>
        {setup && (
          <div>
            <label htmlFor="c" className="mb-1 block text-sm font-medium">Repeat password</label>
            <input id="c" type="password" className={inputCls} value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" required />
          </div>
        )}
        <ErrorNote error={err} />
        <Button variant="primary" type="submit" busy={busy} className="w-full">{setup ? "Create owner account" : "Sign in"}</Button>
      </form>
    </main>
  );
}
