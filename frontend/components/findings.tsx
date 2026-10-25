"use client";

import { Badge } from "@/components/ui";
import type { Finding, Tone } from "@/lib/types";

const tone: Record<string, Tone> = { block: "danger", revise: "warn", info: "neutral" };
const label: Record<string, string> = { block: "must fix", revise: "revise", info: "note" };

/** Findings from a deterministic check, worst first. Waived items show the reason they were accepted. */
export function FindingList({ findings, empty = "No concerns were found by these checks." }: { findings: Finding[]; empty?: string }) {
  if (!findings.length) return <p className="text-sm text-muted">{empty}</p>;
  const order = { block: 0, revise: 1, info: 2 } as const;
  const sorted = [...findings].sort((a, b) => order[a.severity] - order[b.severity]);
  return (
    <ul className="space-y-1.5 text-sm">
      {sorted.map((f, i) => (
        <li key={`${f.code}-${i}`} className="flex gap-2">
          <Badge tone={f.waived ? "ok" : tone[f.severity]}>{f.waived ? "accepted" : label[f.severity]}</Badge>
          <span className="min-w-0">
            <span className="font-medium">{f.field}</span>: {f.message}
            {f.waived && <span className="block text-xs text-muted">Accepted because: {f.waived}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}
