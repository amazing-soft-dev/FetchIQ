import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  activeJobSummary,
  campaignPollInterval,
  jobPollInterval,
  useJob,
  useJobAnalysis,
} from "./queries";
import type { Job } from "./types";

const job: Job = {
  id: "j1",
  kind: "github",
  status: "running",
  hackernews_criteria: null,
  threads: [],
  github_lookups: { done: 0, missing: 0, errors: 0 },
  criteria: {
    countries: ["Germany"],
    us_states: [],
    account_age: { min_years: 0, min_months: 0, max_years: 5, max_months: 0 },
    keywords: [],
    candidate_limit: 100,
  },
  incomplete: false,
  incomplete_reasons: [],
  counts: { discovered: 3, enriched: 1, errors: 0, emails: 1, linkedin_without_email: 0 },
  request_budget: { limit: null, remaining: null, reset_at: null, retry_after_seconds: null },
  overlap_preview: null,
  error: null,
  created_at: null,
  updated_at: null,
};

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return createElement(QueryClientProvider, { client }, children);
}

describe("poll intervals", () => {
  it("polls a job while it is active and stops on a final status", () => {
    expect(jobPollInterval("queued", 2000)).toBe(2000);
    expect(jobPollInterval("running", 4000)).toBe(4000);
    expect(jobPollInterval("paused", 2000)).toBe(2000);
    expect(jobPollInterval("succeeded", 2000)).toBe(false);
    expect(jobPollInterval("failed", 2000)).toBe(false);
    expect(jobPollInterval(undefined, 2000)).toBe(false);
  });

  it("polls a campaign only while it is queued or running", () => {
    expect(campaignPollInterval("queued")).toBe(2000);
    expect(campaignPollInterval("running")).toBe(2000);
    expect(campaignPollInterval("paused")).toBe(false);
    expect(campaignPollInterval("succeeded")).toBe(false);
    expect(campaignPollInterval(undefined)).toBe(false);
  });

  it("picks the first active job from the list", () => {
    expect(
      activeJobSummary([
        { id: "a", kind: "github", status: "succeeded", created_at: null },
        { id: "b", kind: "github", status: "paused", created_at: null },
        { id: "c", kind: "github", status: "running", created_at: null },
      ])?.id,
    ).toBe("b");
    expect(
      activeJobSummary([{ id: "a", kind: "github", status: "failed", created_at: null }]),
    ).toBeUndefined();
  });
});

describe("useJob", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads the job through the API client", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(job), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useJob("j1"), { wrapper });
    await waitFor(() => expect(result.current.data?.id).toBe("j1"));
    expect((fetchMock.mock.calls as unknown as [string][])[0]?.[0]).toBe("/api/jobs/j1");
  });

  it("does not request the analysis until the job is final", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({}), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { result, rerender } = renderHook(
      ({ status }: { status: Job["status"] }) => useJobAnalysis("j1", status),
      { wrapper, initialProps: { status: "running" as Job["status"] } },
    );
    expect(result.current.fetchStatus).toBe("idle");
    expect(fetchMock).not.toHaveBeenCalled();
    rerender({ status: "succeeded" });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect((fetchMock.mock.calls as unknown as [string][])[0]?.[0]).toBe("/api/jobs/j1/analysis");
  });
});
