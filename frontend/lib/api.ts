import type {
  ContactPage,
  ContactQuery,
  GeoCatalog,
  Job,
  JobSummary,
  OutreachStatus,
  Preset,
  SearchCriteria,
} from "./types";

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

function contactParams(query: ContactQuery, includePage: boolean): URLSearchParams {
  const params = new URLSearchParams();
  if (includePage && query.page) {
    params.set("page", String(query.page));
  }
  if (includePage && query.page_size) {
    params.set("page_size", String(query.page_size));
  }
  if (query.email && query.email !== "any") {
    params.set("email", query.email);
  }
  if (query.email_kind && query.email_kind !== "any") {
    params.set("email_kind", query.email_kind);
  }
  if (query.linkedin && query.linkedin !== "any") {
    params.set("linkedin", query.linkedin);
  }
  for (const status of query.status ?? []) {
    params.append("status", status);
  }
  if (query.q) {
    params.set("q", query.q);
  }
  if (query.job_id) {
    params.set("job_id", query.job_id);
  }
  return params;
}

export function listContacts(query: ContactQuery): Promise<ContactPage> {
  const params = contactParams(query, true);
  const suffix = params.size ? `?${params.toString()}` : "";
  return request<ContactPage>(`/api/contacts${suffix}`);
}

export function listContactLogins(query: ContactQuery): Promise<string[]> {
  const params = contactParams(query, false);
  const suffix = params.size ? `?${params.toString()}` : "";
  return request<{ logins: string[] }>(`/api/contacts/logins${suffix}`).then((body) => body.logins);
}

export function listOutreachStatuses(): Promise<OutreachStatus[]> {
  return request<{ statuses: OutreachStatus[] }>("/api/outreach-statuses").then(
    (body) => body.statuses,
  );
}

export function createOutreachStatus(label: string): Promise<OutreachStatus> {
  return request<OutreachStatus>("/api/outreach-statuses", {
    method: "POST",
    body: JSON.stringify({ label }),
  });
}

export function renameOutreachStatus(statusId: string, label: string): Promise<OutreachStatus> {
  return request<OutreachStatus>(`/api/outreach-statuses/${statusId}`, {
    method: "PATCH",
    body: JSON.stringify({ label }),
  });
}

export function deleteOutreachStatus(statusId: string): Promise<void> {
  return request<{ deleted: string }>(`/api/outreach-statuses/${statusId}`, {
    method: "DELETE",
  }).then(() => undefined);
}

export function updateContactStatus(login: string, status: string | null): Promise<void> {
  return request(`/api/contacts/${encodeURIComponent(login)}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  }).then(() => undefined);
}

export function bulkUpdateStatus(logins: string[], status: string | null): Promise<number> {
  return request<{ updated: number }>("/api/contacts/status", {
    method: "POST",
    body: JSON.stringify({ logins, status }),
  }).then((body) => body.updated);
}

export function listJobs(): Promise<JobSummary[]> {
  return request<{ jobs: JobSummary[] }>("/api/jobs").then((body) => body.jobs);
}

export async function downloadContacts(logins: string[]): Promise<void> {
  const response = await fetch("/api/contacts/export.csv", {
    method: "POST",
    headers: { Accept: "text/csv", "Content-Type": "application/json" },
    body: JSON.stringify({ logins }),
  });
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "fetchiq-contacts.csv";
  link.click();
  URL.revokeObjectURL(url);
}

export function exportHref(jobId: string, includeNoContact: boolean): string {
  const params = new URLSearchParams({
    include_no_contact: includeNoContact ? "true" : "false",
  });
  return `/api/jobs/${jobId}/export.csv?${params.toString()}`;
}
