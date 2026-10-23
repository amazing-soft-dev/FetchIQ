export type AccountAge = {
  min_years: number;
  min_months: number;
  max_years: number;
  max_months: number;
};

export type SearchCriteria = {
  countries: string[];
  us_states: string[];
  account_age: AccountAge;
  keywords: string[];
  candidate_limit: number;
};

export type JobStatus =
  | "queued"
  | "running"
  | "paused"
  | "incomplete"
  | "succeeded"
  | "failed"
  | "cancelled";

export type Job = {
  id: string;
  status: JobStatus;
  criteria: SearchCriteria;
  incomplete: boolean;
  incomplete_reasons: string[];
  counts: {
    discovered: number;
    enriched: number;
    errors: number;
    emails: number;
    linkedin_without_email: number;
  };
  error: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type Preset = {
  id: string;
  name: string;
  criteria: SearchCriteria;
  created_at: string | null;
};

export type OutreachStatus = {
  id: string;
  label: string;
  position: number;
};

export type Contact = {
  login: string;
  name: string | null;
  profile_url: string;
  public_email: string | null;
  email_kinds: Array<"personal" | "other">;
  linkedin_url: string | null;
  country: string | null;
  outreach_status: string | null;
  last_checked_at: string | null;
  job_id: string | null;
};

export type ContactQuery = {
  page?: number;
  page_size?: number;
  email?: "any" | "has" | "missing";
  email_kind?: "any" | "personal" | "other";
  linkedin?: "any" | "has" | "missing";
  status?: string[];
  country?: string[];
  q?: string;
  job_id?: string;
};

export type ContactPage = {
  contacts: Contact[];
  total: number;
  page: number;
  page_size: number;
};

export type JobSummary = {
  id: string;
  status: JobStatus;
  created_at: string | null;
};

export type CampaignStatus = "queued" | "running" | "paused" | "succeeded" | "failed" | "cancelled";

export type Campaign = {
  id: string;
  status: CampaignStatus;
  subject: string;
  body: string;
  hourly_limit: number;
  delay_min_seconds: number;
  delay_max_seconds: number;
  counts: {
    queued: number;
    sent: number;
    failed: number;
    skipped: number;
  };
  failures: Array<{ login: string; error: string | null }>;
  error: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type MailCheckResult =
  | { ok: true; from: string; host: string; port: number }
  | { ok: false; detail: string };

export type CampaignDraft = {
  logins: string[];
  subject: string;
  body: string;
  hourly_limit: number;
  delay_min_seconds: number;
  delay_max_seconds: number;
};

export type GeoCatalog = {
  countries: string[];
  us_states: string[];
};

export const UNITED_STATES = "United States";

export const emptyCriteria = (): SearchCriteria => ({
  countries: [],
  us_states: [],
  account_age: { min_years: 0, min_months: 0, max_years: 5, max_months: 0 },
  keywords: [],
  candidate_limit: 100,
});
