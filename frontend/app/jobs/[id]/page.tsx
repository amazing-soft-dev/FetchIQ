import { JobPanel } from "../../../components/JobPanel";

export default async function JobPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <JobPanel jobId={id} />;
}
