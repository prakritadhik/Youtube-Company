"use client";

import { useApi } from "@/lib/api";
import { Badge, Card } from "@/components/ui";

interface VideoExperiment { experiment: string; title: string; status: string; arm: string; instruction: string }

/** Shown on a video's page only when the video is part of a running experiment: which arm it is in and exactly what to do. */
export function VideoExperimentNote({ videoId }: { videoId: number }) {
  const { data } = useApi<{ experiments: VideoExperiment[] }>(`/api/videos/${videoId}/experiments`);
  if (!data || data.experiments.length === 0) return null;
  return (
    <Card title="Experiment" subtitle="This video takes part in a test. The arm was drawn at random before publication and cannot be changed.">
      <ul className="space-y-3 text-sm">
        {data.experiments.map((x) => (
          <li key={x.experiment} className="rounded-xl border border-line p-3">
            <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{x.experiment}</span><span>{x.title}</span><Badge tone={x.arm === "treatment" ? "info" : "neutral"}>{x.arm}</Badge></div>
            <p className="mt-1">{x.instruction}</p>
          </li>
        ))}
      </ul>
    </Card>
  );
}
