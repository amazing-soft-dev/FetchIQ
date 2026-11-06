import "server-only";

import type { GeoCatalog, Job, OutreachStatus, Preset } from "./types";

// Server components talk to the backend directly: the /api rewrite only exists in the
// browser. Same base as the rewrite target, same token as the browser sends.
const BASE = (process.env.API_PROXY_TARGET || "http://127.0.0.1:8000").replace(/\/+$/, "");
const TOKEN = (process.env.API_TOKEN || process.env.NEXT_PUBLIC_API_TOKEN || "").trim();

export type ServerResult<T> = { data: T } | { missing: true } | { unavailable: true };

/** GET a JSON route for a first render. Never throws: the page still renders without it. */
export async function serverGet<T>(path: string): Promise<ServerResult<T>> {
  try {
    const response = await fetch(`${BASE}${path}`, {
      headers: {
        Accept: "application/json",
        ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}),
      },
      cache: "no-store",
    });
    if (response.status === 404) {
      return { missing: true };
    }
    if (!response.ok) {
      return { unavailable: true };
    }
    return { data: (await response.json()) as T };
  } catch {
    return { unavailable: true };
  }
}

function dataOrUndefined<T>(result: ServerResult<T>): T | undefined {
  return "data" in result ? result.data : undefined;
}

export async function loadGeo(): Promise<GeoCatalog | undefined> {
  return dataOrUndefined(await serverGet<GeoCatalog>("/api/geo"));
}

export async function loadPresets(): Promise<Preset[] | undefined> {
  const result = await serverGet<{ presets: Preset[] }>("/api/presets");
  return dataOrUndefined(result)?.presets;
}

export async function loadOutreachStatuses(): Promise<OutreachStatus[] | undefined> {
  const result = await serverGet<{ statuses: OutreachStatus[] }>("/api/outreach-statuses");
  return dataOrUndefined(result)?.statuses;
}

export async function loadJob(jobId: string): Promise<ServerResult<Job>> {
  return serverGet<Job>(`/api/jobs/${encodeURIComponent(jobId)}`);
}
