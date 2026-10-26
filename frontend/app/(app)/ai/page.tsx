"use client";

import { useApi } from "@/lib/api";
import { useAuth } from "@/components/auth";
import { ActivityTable, AskForIdeas, BrainCard } from "@/components/ai";
import { Card, PageHeader } from "@/components/ui";

interface Channel { slug: string; name: string; kind: string }

export default function AiPage() {
  const { can } = useAuth();
  const ch = useApi<Channel[]>("/api/channels");
  return (
    <>
      <PageHeader title="AI brain"
        description="The writing and the managers’ reviews are done by Claude Code on this computer, signed in with your own Claude account. Everything it writes is checked by the company’s own rules, and you still decide rights concerns, medical review and publication." />
      <div className="space-y-6">
        {can("settings:read") && <BrainCard />}
        {can("task:create") && ch.data && <AskForIdeas channels={ch.data} />}
        <Card title="Everything the brain has done" subtitle="Each call, what it was for and how it ended. Open one to see exactly what was sent and what came back.">
          <ActivityTable />
        </Card>
      </div>
    </>
  );
}
