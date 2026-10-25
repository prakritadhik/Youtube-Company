"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";
import { useAuth } from "@/components/auth";
import { Banner, Button, Loading } from "@/components/ui";
import { fmtDate } from "@/lib/format";

const NAV: { href: string; label: string; icon: string; perm?: string }[] = [
  { href: "/", label: "Overview", icon: "◧" },
  { href: "/channels/happy-birthday-songs", label: "Birthday Songs", icon: "♪" },
  { href: "/channels/medicine-information", label: "Medicine Info", icon: "✚" },
  { href: "/channels/ai-story-videos", label: "AI Stories", icon: "✦" },
  { href: "/plan", label: "Content Plan", icon: "✎" },
  { href: "/organization", label: "Organization", icon: "⌘" },
  { href: "/board", label: "Intelligence Board", icon: "▤" },
  { href: "/approvals", label: "Approvals", icon: "✓" },
  { href: "/performance", label: "Performance", icon: "↗" },
  { href: "/comments", label: "Comments", icon: "☰" },
  { href: "/learning", label: "Learning", icon: "✧" },
  { href: "/operations", label: "Operations", icon: "◔" },
  { href: "/rights", label: "Originality & Rights", icon: "©" },
  { href: "/reports", label: "Executive Reports", icon: "≡" },
  { href: "/settings", label: "Settings & Control", icon: "⚙", perm: "settings:read" },
  { href: "/diagnostics", label: "Diagnostics", icon: "⌥", perm: "diagnostics:read" },
];

interface ControlStatus {
  operating_mode: string;
  emergency_pause: { active: boolean; since: string | null; by: string | null; reason: string | null };
}

export function Shell({ children }: { children: ReactNode }) {
  const { loading, authenticated, initialized, user, refresh, can } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const { data: ctl, reload: reloadControl } = useApi<ControlStatus>(authenticated ? "/api/control/status" : null, { refreshMs: 15000 });
  const { data: ops } = useApi<{ alerts: { critical: number; warning: number; info: number; open: number }; worker_alive: boolean }>(authenticated ? "/api/ops/summary" : null, { refreshMs: 30000 });

  // Pause/resume must be reflected immediately, not on the next poll.
  useEffect(() => {
    window.addEventListener("control:changed", reloadControl);
    return () => window.removeEventListener("control:changed", reloadControl);
  }, [reloadControl]);

  useEffect(() => {
    if (!loading && !authenticated) router.replace("/login");
  }, [loading, authenticated, router]);
  useEffect(() => setOpen(false), [pathname]);

  if (loading || !authenticated) return <Loading label={initialized ? "Checking your session…" : "Loading…"} />;

  const signOut = async () => {
    try { await api("/api/auth/logout", { method: "POST", body: {} }); } finally { await refresh(); router.replace("/login"); }
  };
  const paused = ctl?.emergency_pause.active;
  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[17rem_1fr]">
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-surface px-4 py-3 lg:hidden">
        <span className="font-semibold">AI Media Company</span>
        <Button variant="ghost" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label="Toggle navigation">☰</Button>
      </header>

      <aside className={`${open ? "block" : "hidden"} border-r border-line bg-surface lg:sticky lg:top-0 lg:block lg:h-screen lg:overflow-y-auto`}>
        <div className="hidden px-6 pb-2 pt-6 lg:block">
          <div className="text-lg font-semibold tracking-tight">AI Media Company</div>
          <div className="text-xs text-muted">Owner dashboard · localhost</div>
        </div>
        <nav className="space-y-0.5 p-3" aria-label="Main">
          {NAV.filter((n) => !n.perm || can(n.perm)).map((n) => (
            <Link key={n.href} href={n.href} aria-current={isActive(n.href) ? "page" : undefined}
              className={`flex items-center gap-3 rounded-xl px-3 py-2 text-sm transition ${isActive(n.href) ? "bg-accent-soft font-medium text-accent" : "text-ink/80 hover:bg-surface2"}`}>
              <span aria-hidden className="w-5 text-center">{n.icon}</span>{n.label}
              {n.href === "/operations" && ops && ops.alerts.open > 0 && (
                <span className={`ml-auto rounded-full px-2 py-0.5 text-xs font-medium ${ops.alerts.critical ? "bg-danger-soft text-danger" : ops.alerts.warning ? "bg-warn-soft text-warn" : "bg-surface2 text-muted"}`}
                  aria-label={`${ops.alerts.open} open alerts`}>{ops.alerts.open}</span>
              )}
            </Link>
          ))}
        </nav>
        <div className="mt-auto border-t border-line p-4 text-sm">
          <div className="font-medium">{user?.username}</div>
          <div className="text-xs capitalize text-muted">{user?.role}</div>
          <Button variant="ghost" className="mt-2 -ml-3" onClick={signOut}>Sign out</Button>
        </div>
      </aside>

      <main className="min-w-0">
        {paused && (
          <div className="border-b border-danger/30 bg-danger-soft px-6 py-3 text-sm text-danger" role="alert">
            <strong>Emergency pause is active.</strong> No new work, paid generation, uploads or replies are starting.
            {ctl?.emergency_pause.reason && <> Reason: {ctl.emergency_pause.reason}.</>}
            {ctl?.emergency_pause.since && <> Since {fmtDate(ctl.emergency_pause.since)}.</>}
          </div>
        )}
        {ops && !ops.worker_alive && (
          <div className="border-b border-warn/30 bg-warn-soft px-6 py-2 text-sm text-warn" role="status">
            The background worker is not running, so queued and scheduled work is waiting. Start everything with <code>python launcher.py</code>.
          </div>
        )}
        {ctl && !paused && ctl.operating_mode === "DEVELOPMENT" && (
          <div className="border-b border-line bg-info-soft px-6 py-2 text-xs text-info">
            DEVELOPMENT mode — real publishing and public replies are disabled.
          </div>
        )}
        <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">{children}</div>
      </main>
    </div>
  );
}

export { Banner };
