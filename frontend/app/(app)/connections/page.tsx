"use client";

import { ConnectionsList } from "@/components/connections";
import { PageHeader } from "@/components/ui";

export default function ConnectionsPage() {
  return (
    <>
      <PageHeader title="Connections"
        description="Connect each outside service here, in order. Keys you paste are stored encrypted on this computer and are used straight away: no restart and no file to edit. Only you can change them, and every change is recorded." />
      <ConnectionsList />
    </>
  );
}
