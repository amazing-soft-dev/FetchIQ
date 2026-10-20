"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { createJob, createPreset, getGeo, listPresets, startJob } from "../lib/api";
import {
  UNITED_STATES,
  emptyCriteria,
  type AccountAge,
  type Preset,
  type SearchCriteria,
} from "../lib/types";
import { ChipMultiSelect } from "./ChipMultiSelect";

function ageInMonths(years: number, months: number): number {
  return years * 12 + months;
}

function criteriaError(criteria: SearchCriteria): string | null {
  if (criteria.countries.length === 0) {
    return "Select at least one country.";
  }
  const age = criteria.account_age;
  const fields = [age.min_years, age.min_months, age.max_years, age.max_months];
  if (fields.some((value) => !Number.isInteger(value) || value < 0)) {
    return "Account age must be a whole number of years and months.";
  }
  if (age.min_months > 11 || age.max_months > 11 || age.min_years > 100 || age.max_years > 100) {
    return "Years must be 0 to 100 and months must be 0 to 11.";
  }
  if (ageInMonths(age.min_years, age.min_months) > ageInMonths(age.max_years, age.max_months)) {
    return "Minimum account age must be less than or equal to the maximum.";
  }
  const limit = criteria.candidate_limit;
  if (!Number.isInteger(limit) || limit < 1 || limit > 5000) {
    return "Candidate limit must be between 1 and 5000.";
  }
  return null;
}

function parseKeywords(value: string): string[] {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

const AGE_DEFAULTS: AccountAge = emptyCriteria().account_age;
const LIMIT_DEFAULT = emptyCriteria().candidate_limit;

function emptyAgeInputs(): Record<keyof AccountAge, string> {
  return { min_years: "", min_months: "", max_years: "", max_months: "" };
}

function parseAgeField(raw: string, fallback: number): number {
  const text = raw.trim();
  if (text === "") {
    return fallback;
  }
  return Number(text);
}

function accountAgeFromInputs(inputs: Record<keyof AccountAge, string>): AccountAge {
  return {
    min_years: parseAgeField(inputs.min_years, AGE_DEFAULTS.min_years),
    min_months: parseAgeField(inputs.min_months, AGE_DEFAULTS.min_months),
    max_years: parseAgeField(inputs.max_years, AGE_DEFAULTS.max_years),
    max_months: parseAgeField(inputs.max_months, AGE_DEFAULTS.max_months),
  };
}

export function SearchForm() {
  const router = useRouter();
  const [catalog, setCatalog] = useState<{ countries: string[]; us_states: string[] } | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [criteria, setCriteria] = useState<SearchCriteria>(emptyCriteria);
  const [ageInputs, setAgeInputs] = useState(emptyAgeInputs);
  const [limitInput, setLimitInput] = useState("");
  const [keywordText, setKeywordText] = useState("");
  const [presetName, setPresetName] = useState("");
  const [presetVersion, setPresetVersion] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getGeo(), listPresets()])
      .then(([geo, saved]) => {
        if (!cancelled) {
          setCatalog(geo);
          setPresets(saved);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "Could not load search options.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const unitedStatesSelected = criteria.countries.includes(UNITED_STATES);

  function currentCriteria(): SearchCriteria {
    return {
      ...criteria,
      us_states: unitedStatesSelected ? criteria.us_states : [],
      keywords: parseKeywords(keywordText),
      account_age: accountAgeFromInputs(ageInputs),
      candidate_limit: limitInput.trim() === "" ? LIMIT_DEFAULT : Number(limitInput),
    };
  }

  function setAgeInput(field: keyof AccountAge, value: string) {
    setAgeInputs((current) => ({ ...current, [field]: value }));
  }

  function setCountries(countries: string[]) {
    setCriteria((current) => ({
      ...current,
      countries,
      us_states: countries.includes(UNITED_STATES) ? current.us_states : [],
    }));
  }

  function setStates(usStates: string[]) {
    setCriteria((current) => ({ ...current, us_states: usStates }));
  }

  function loadPreset(preset: Preset) {
    const age = preset.criteria.account_age;
    setCriteria(preset.criteria);
    setAgeInputs({
      min_years: String(age.min_years),
      min_months: String(age.min_months),
      max_years: String(age.max_years),
      max_months: String(age.max_months),
    });
    setLimitInput(String(preset.criteria.candidate_limit));
    setKeywordText(preset.criteria.keywords.join(", "));
    setPresetVersion((version) => version + 1);
    setError(null);
  }

  async function savePreset() {
    const next = currentCriteria();
    const problem = criteriaError(next);
    if (problem) {
      setError(problem);
      return;
    }
    if (!presetName.trim()) {
      setError("Name the preset before saving it.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const saved = await createPreset(presetName, next);
      setPresets((current) => [saved, ...current.filter((item) => item.id !== saved.id)]);
      setPresetName("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save the preset.");
    } finally {
      setBusy(false);
    }
  }

  async function startSearch() {
    const next = currentCriteria();
    const problem = criteriaError(next);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const job = await createJob(next);
      await startJob(job.id);
      router.push(`/jobs/${job.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start the search.");
      setBusy(false);
    }
  }

  return (
    <form
      className="panel"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        void startSearch();
      }}
    >
      <div className="panel-head">
        <h1>Search public GitHub profiles</h1>
        <p>
          Location and account age seed the search. Contact fields are filled only from what a
          developer has published on their profile or in a public README.
        </p>
      </div>

      {error ? <p className="error">{error}</p> : null}

      <ChipMultiSelect
        key={`countries-${presetVersion}`}
        label="Location"
        helper="Select one or more countries or locations"
        placeholder="Search and add locations..."
        options={catalog?.countries ?? []}
        value={criteria.countries}
        onChange={setCountries}
        countNoun="location"
      />

      {unitedStatesSelected ? (
        <ChipMultiSelect
          key={`states-${presetVersion}`}
          label="US states"
          placeholder="Search and add states..."
          options={catalog?.us_states ?? []}
          value={criteria.us_states}
          onChange={setStates}
          countNoun="state"
        />
      ) : null}

      <fieldset>
        <legend>Account age</legend>
        <div className="age-grid">
          <label>
            Minimum years
            <input
              type="number"
              min={0}
              max={100}
              placeholder={String(AGE_DEFAULTS.min_years)}
              value={ageInputs.min_years}
              onChange={(event) => setAgeInput("min_years", event.target.value)}
            />
          </label>
          <label>
            Minimum months
            <input
              type="number"
              min={0}
              max={11}
              placeholder={String(AGE_DEFAULTS.min_months)}
              value={ageInputs.min_months}
              onChange={(event) => setAgeInput("min_months", event.target.value)}
            />
          </label>
          <label>
            Maximum years
            <input
              type="number"
              min={0}
              max={100}
              placeholder={String(AGE_DEFAULTS.max_years)}
              value={ageInputs.max_years}
              onChange={(event) => setAgeInput("max_years", event.target.value)}
            />
          </label>
          <label>
            Maximum months
            <input
              type="number"
              min={0}
              max={11}
              placeholder={String(AGE_DEFAULTS.max_months)}
              value={ageInputs.max_months}
              onChange={(event) => setAgeInput("max_months", event.target.value)}
            />
          </label>
        </div>
      </fieldset>

      <label>
        Keywords
        <input
          type="text"
          value={keywordText}
          placeholder="Optional, comma-separated"
          onChange={(event) => setKeywordText(event.target.value)}
        />
      </label>

      <label>
        Candidate limit
        <input
          type="number"
          min={1}
          max={5000}
          placeholder={String(LIMIT_DEFAULT)}
          value={limitInput}
          onChange={(event) => setLimitInput(event.target.value)}
        />
      </label>

      <div className="actions">
        <button type="submit" disabled={busy}>
          {busy ? "Starting…" : "Start search"}
        </button>
      </div>

      <fieldset>
        <legend>Presets</legend>
        <div className="preset-row">
          <label>
            Preset name
            <input
              type="text"
              value={presetName}
              maxLength={80}
              onChange={(event) => setPresetName(event.target.value)}
            />
          </label>
          <button type="button" onClick={() => void savePreset()} disabled={busy}>
            Save preset
          </button>
        </div>
        {presets.length === 0 ? (
          <p className="muted">No saved presets.</p>
        ) : (
          <ul className="preset-list">
            {presets.map((preset) => (
              <li key={preset.id}>
                <button type="button" onClick={() => loadPreset(preset)}>
                  {preset.name}
                </button>
              </li>
            ))}
          </ul>
        )}
      </fieldset>
    </form>
  );
}
