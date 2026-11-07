// Response and request shapes come from the backend's OpenAPI document through
// lib/api-types.ts (generated; see CONTRIBUTING). Only the names used by the UI
// are re-exported here, plus the two values that are not part of the API.
import type { components } from "./api-types";

type Schemas = components["schemas"];

export type AccountAge = Schemas["AccountAge-Output"];
export type SearchCriteria = Schemas["SearchCriteria-Output"];

export type HackerNewsCriteria = Schemas["HackerNewsCriteria-Output"];

export type Job = Schemas["JobOut"];
export type JobStatus = Job["status"];
export type JobKind = Job["kind"];
export type ThreadReport = Schemas["ThreadReport"];
export type JobSummary = Schemas["JobSummary"];
export type OverlapJob = Schemas["OverlapJob"];
export type OverlapPreview = Schemas["OverlapPreview"];
export type PlaceReport = Schemas["PlaceReport"];
export type SliceCap = Schemas["SliceCap"];
export type SearchAnalysis = Schemas["AnalysisOut"];

export type Preset = Schemas["PresetOut"];
export type GeoCatalog = Schemas["GeoCatalog"];

export type OutreachStatus = Schemas["OutreachStatusOut"];
export type Contact = Schemas["ContactOut"];
export type ContactPage = Schemas["ContactPageOut"];
export type ContactLogins = Schemas["ContactLoginsOut"];

export type Campaign = Schemas["CampaignOut"];
export type CampaignStatus = Campaign["status"];
export type CampaignDraft = Schemas["CampaignCreate"];
export type MailCheckResult = Schemas["MailCheckOk"] | Schemas["MailCheckFailed"];

/** Query parameters for GET /api/contacts and /api/contacts/logins. */
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
  source?: "any" | "github" | "hackernews";
};

export const UNITED_STATES = "United States";

export const emptyHackerNewsCriteria = (): HackerNewsCriteria => ({
  months_back: 1,
  countries: [],
  keywords: [],
  remote_only: false,
  github_lookup: true,
});

export const emptyCriteria = (): SearchCriteria => ({
  countries: [],
  us_states: [],
  account_age: { min_years: 0, min_months: 0, max_years: 5, max_months: 0 },
  keywords: [],
  candidate_limit: 100,
});
