import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getActiveCampaign,
  getAnalysis,
  getCampaign,
  getGeo,
  getJob,
  listJobs,
  listOutreachStatuses,
  listPresets,
  postCampaignAction,
  postJobAction,
} from "./api";
import type {
  Campaign,
  CampaignStatus,
  GeoCatalog,
  Job,
  JobStatus,
  JobSummary,
  OutreachStatus,
  Preset,
} from "./types";

export const ACTIVE_JOB_STATUSES: readonly JobStatus[] = ["queued", "running", "paused"];
export const TERMINAL_JOB_STATUSES: readonly JobStatus[] = [
  "succeeded",
  "incomplete",
  "failed",
  "cancelled",
];
export const ACTIVE_CAMPAIGN_STATUSES: readonly CampaignStatus[] = ["queued", "running", "paused"];

export const JOB_POLL_MS = 2000;
export const HEADER_POLL_MS = 4000;
export const CAMPAIGN_POLL_MS = 2000;

export const jobKeys = {
  all: ["jobs"] as const,
  list: () => ["jobs", "list"] as const,
  detail: (jobId: string) => ["jobs", "detail", jobId] as const,
  analysis: (jobId: string) => ["jobs", "analysis", jobId] as const,
};

export const catalogKeys = {
  geo: () => ["geo"] as const,
  presets: () => ["presets"] as const,
  statuses: () => ["outreach-statuses"] as const,
};

export const campaignKeys = {
  active: () => ["campaigns", "active"] as const,
  detail: (campaignId: string) => ["campaigns", "detail", campaignId] as const,
};

/** How often to poll a job, or false once it has reached a final status. */
export function jobPollInterval(status: JobStatus | undefined, intervalMs: number): number | false {
  return status !== undefined && ACTIVE_JOB_STATUSES.includes(status) ? intervalMs : false;
}

/** A paused campaign changes only when the user acts, so polling stops there too. */
export function campaignPollInterval(status: CampaignStatus | undefined): number | false {
  return status === "queued" || status === "running" ? CAMPAIGN_POLL_MS : false;
}

export function activeJobSummary(jobs: JobSummary[]): JobSummary | undefined {
  return jobs.find((job) => ACTIVE_JOB_STATUSES.includes(job.status));
}

/** Server components pass data they already fetched; the query starts from it. */
function seeded<T>(initial: T | undefined): { initialData: T } | Record<never, never> {
  return initial !== undefined ? { initialData: initial } : {};
}

export function useJob(
  jobId: string,
  options: { intervalMs?: number; enabled?: boolean; initialData?: Job | undefined } = {},
) {
  const intervalMs = options.intervalMs ?? JOB_POLL_MS;
  return useQuery({
    queryKey: jobKeys.detail(jobId),
    queryFn: () => getJob(jobId),
    enabled: options.enabled ?? true,
    ...seeded(options.initialData),
    refetchInterval: (query) => jobPollInterval(query.state.data?.status, intervalMs),
  });
}

export function useJobAnalysis(jobId: string, status: JobStatus | undefined) {
  return useQuery({
    queryKey: jobKeys.analysis(jobId),
    queryFn: () => getAnalysis(jobId),
    enabled: status !== undefined && TERMINAL_JOB_STATUSES.includes(status),
    staleTime: Infinity,
  });
}

/** The newest active job, for the header link. Polls the list, then shares the job entry. */
export function useActiveJob() {
  const list = useQuery({
    queryKey: jobKeys.list(),
    queryFn: listJobs,
    refetchInterval: HEADER_POLL_MS,
    select: activeJobSummary,
  });
  const activeId = list.data?.id;
  const job = useJob(activeId ?? "", { enabled: activeId !== undefined, intervalMs: HEADER_POLL_MS });
  const current = activeId !== undefined ? job.data : undefined;
  return current && ACTIVE_JOB_STATUSES.includes(current.status) ? current : null;
}

export function useJobAction(jobId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (action: "start" | "pause" | "resume" | "cancel") => postJobAction(jobId, action),
    onSuccess: (job: Job) => {
      client.setQueryData(jobKeys.detail(jobId), job);
      void client.invalidateQueries({ queryKey: jobKeys.list() });
    },
  });
}

export function useActiveCampaign() {
  return useQuery({
    queryKey: campaignKeys.active(),
    queryFn: getActiveCampaign,
    staleTime: Infinity,
  });
}

export function useCampaign(campaignId: string | null, status: CampaignStatus | undefined) {
  return useQuery({
    queryKey: campaignKeys.detail(campaignId ?? ""),
    queryFn: () => getCampaign(campaignId ?? ""),
    enabled: campaignId !== null,
    refetchInterval: (query) => campaignPollInterval(query.state.data?.status ?? status),
  });
}

export function useCampaignAction() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      campaignId,
      action,
    }: {
      campaignId: string;
      action: "pause" | "resume" | "cancel";
    }) => postCampaignAction(campaignId, action),
    onSuccess: (campaign: Campaign) => {
      client.setQueryData(campaignKeys.detail(campaign.id), campaign);
    },
  });
}

export function useSeedCampaign() {
  const client = useQueryClient();
  return (campaign: Campaign) => {
    client.setQueryData(campaignKeys.detail(campaign.id), campaign);
    client.setQueryData(campaignKeys.active(), campaign);
  };
}

// Catalogs: small lists that change only when the user edits them, so the first
// render's copy (from the server component) is kept and edits update the cache.
const CATALOG_STALE_MS = 60_000;

export function useGeo(initial?: GeoCatalog | undefined) {
  return useQuery({
    queryKey: catalogKeys.geo(),
    queryFn: getGeo,
    staleTime: Infinity,
    ...seeded(initial),
  });
}

export function usePresets(initial?: Preset[] | undefined) {
  return useQuery({
    queryKey: catalogKeys.presets(),
    queryFn: listPresets,
    staleTime: CATALOG_STALE_MS,
    ...seeded(initial),
  });
}

export function useOutreachStatuses(initial?: OutreachStatus[] | undefined) {
  return useQuery({
    queryKey: catalogKeys.statuses(),
    queryFn: listOutreachStatuses,
    staleTime: CATALOG_STALE_MS,
    ...seeded(initial),
  });
}
