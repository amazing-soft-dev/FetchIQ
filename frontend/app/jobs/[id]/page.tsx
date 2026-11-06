import { notFound } from "next/navigation";

import { JobPanel } from "../../../components/JobPanel";
import { loadJob } from "../../../lib/server-api";

export default async function JobPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const result = await loadJob(id);
  if ("missing" in result) {
    notFound();
  }
  return <JobPanel jobId={id} initialJob={"data" in result ? result.data : undefined} />;
}
