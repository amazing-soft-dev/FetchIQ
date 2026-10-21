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
