import type { GeoCatalog, Job, Preset, SearchCriteria } from "./types";

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") {
      return body.detail;
    }
    if (Array.isArray(body.detail)) {
      return body.detail
        .map((item) => {
          if (item && typeof item === "object" && "msg" in item) {
            return String(item.msg);
          }
          return "";
        })
        .filter(Boolean)
        .join(" ");
    }
  } catch {
    return `Request failed (${response.status})`;
  }
  return `Request failed (${response.status})`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
  return (await response.json()) as T;
}

export function getGeo(): Promise<GeoCatalog> {
  return request<GeoCatalog>("/api/geo");
}

export function listPresets(): Promise<Preset[]> {
  return request<{ presets: Preset[] }>("/api/presets").then((body) => body.presets);
}

export function createPreset(name: string, criteria: SearchCriteria): Promise<Preset> {
  return request<Preset>("/api/presets", {
    method: "POST",
    body: JSON.stringify({ name, criteria }),
  });
}

export function createJob(criteria: SearchCriteria): Promise<Job> {
  return request<Job>("/api/jobs", {
    method: "POST",
    body: JSON.stringify({ criteria }),
  });
}

export function getJob(jobId: string): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}`);
}

export function postJobAction(
  jobId: string,
  action: "start" | "pause" | "resume" | "cancel",
): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}/${action}`, { method: "POST" });
}

export function startJob(jobId: string): Promise<Job> {
  return postJobAction(jobId, "start");
}

export function exportHref(jobId: string, includeNoContact: boolean): string {
  const params = new URLSearchParams({
    include_no_contact: includeNoContact ? "true" : "false",
  });
  return `/api/jobs/${jobId}/export.csv?${params.toString()}`;
}
