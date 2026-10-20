"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { createJob, createPreset, getGeo, listPresets, startJob } from "../lib/api";
import { UNITED_STATES, emptyCriteria, type Preset, type SearchCriteria } from "../lib/types";

function ageInMonths(years: number, months: number): number {
  return years * 12 + months;
}

type AgeDraft = {
  min_years: string;
  min_months: string;
  max_years: string;
  max_months: string;
};

const EMPTY_AGE: AgeDraft = {
  min_years: "",
  min_months: "",
  max_years: "",
  max_months: "",
};

function ageDraftFrom(age: SearchCriteria["account_age"]): AgeDraft {
  return {
    min_years: String(age.min_years),
    min_months: String(age.min_months),
    max_years: String(age.max_years),
    max_months: String(age.max_months),
  };
}

function parseAge(draft: AgeDraft): { error: string } | { age: SearchCriteria["account_age"] } {
  const fields = [
    ["min_years", draft.min_years, 100],
    ["min_months", draft.min_months, 11],
    ["max_years", draft.max_years, 100],
    ["max_months", draft.max_months, 11],
  ] as const;
  const parsed: Partial<SearchCriteria["account_age"]> = {};
  for (const [key, raw, max] of fields) {
    const text = raw.trim();
    if (text === "") {
      return { error: "Account age is required." };
    }
    if (!/^\d+$/.test(text)) {
      return { error: "Account age must be a whole number of years and months." };
    }
    const value = Number(text);
    if (value > max) {
      return { error: "Years must be 0 to 100 and months must be 0 to 11." };
    }
    parsed[key] = value;
  }
  const age = parsed as SearchCriteria["account_age"];
  if (ageInMonths(age.min_years, age.min_months) > ageInMonths(age.max_years, age.max_months)) {
    return { error: "Minimum account age must be less than or equal to the maximum." };
  }
  return { age };
}

function parseKeywords(value: string): string[] {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

export function SearchForm() {
  const router = useRouter();
  const [catalog, setCatalog] = useState<{ countries: string[]; us_states: string[] } | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [criteria, setCriteria] = useState<SearchCriteria>(emptyCriteria);
  const [ageDraft, setAgeDraft] = useState<AgeDraft>(EMPTY_AGE);
  const [keywordText, setKeywordText] = useState("");
  const [countryQuery, setCountryQuery] = useState("");
  const [stateQuery, setStateQuery] = useState("");
  const [presetName, setPresetName] = useState("");
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

  const filteredCountries = useMemo(() => {
    const query = countryQuery.trim().toLowerCase();
    return (catalog?.countries ?? []).filter((country) => country.toLowerCase().includes(query));
  }, [catalog, countryQuery]);

  const filteredStates = useMemo(() => {
    const query = stateQuery.trim().toLowerCase();
    return (catalog?.us_states ?? []).filter((state) => state.toLowerCase().includes(query));
  }, [catalog, stateQuery]);

  const unitedStatesSelected = criteria.countries.includes(UNITED_STATES);

  function readyCriteria(): { error: string } | { criteria: SearchCriteria } {
    if (criteria.countries.length === 0) {
      return { error: "Select at least one country." };
    }
    const parsed = parseAge(ageDraft);
    if ("error" in parsed) {
      return parsed;
    }
    const limit = criteria.candidate_limit;
    if (!Number.isInteger(limit) || limit < 1 || limit > 5000) {
      return { error: "Candidate limit must be between 1 and 5000." };
    }
    return {
      criteria: {
        ...criteria,
        account_age: parsed.age,
        us_states: unitedStatesSelected ? criteria.us_states : [],
        keywords: parseKeywords(keywordText),
      },
    };
  }

  function toggleCountry(country: string) {
    setCriteria((current) => {
      const selected = current.countries.includes(country)
        ? current.countries.filter((item) => item !== country)
        : [...current.countries, country];
      return {
        ...current,
        countries: selected,
        us_states: selected.includes(UNITED_STATES) ? current.us_states : [],
      };
    });
  }

  function toggleState(state: string) {
    setCriteria((current) => {
      const selected = current.us_states.includes(state)
        ? current.us_states.filter((item) => item !== state)
        : [...current.us_states, state];
      return { ...current, us_states: selected };
    });
  }

  function loadPreset(preset: Preset) {
    setCriteria(preset.criteria);
    setAgeDraft(ageDraftFrom(preset.criteria.account_age));
    setKeywordText(preset.criteria.keywords.join(", "));
    setError(null);
  }

  async function savePreset() {
    const ready = readyCriteria();
    if ("error" in ready) {
      setError(ready.error);
      return;
    }
    const next = ready.criteria;
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
    const ready = readyCriteria();
    if ("error" in ready) {
      setError(ready.error);
      return;
    }
    const next = ready.criteria;
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

      <fieldset>
        <legend>Countries</legend>
        <input
          type="search"
          value={countryQuery}
          placeholder="Filter countries"
          aria-label="Filter countries"
          onChange={(event) => setCountryQuery(event.target.value)}
        />
        <div className="choice-list" role="group" aria-label="Countries">
          {filteredCountries.map((country) => (
            <label key={country}>
              <input
                type="checkbox"
                checked={criteria.countries.includes(country)}
                onChange={() => toggleCountry(country)}
              />
              {country}
            </label>
          ))}
        </div>
      </fieldset>

      {unitedStatesSelected ? (
        <fieldset>
          <legend>US states</legend>
          <input
            type="search"
            value={stateQuery}
            placeholder="Filter states"
            aria-label="Filter US states"
            onChange={(event) => setStateQuery(event.target.value)}
          />
          <div className="choice-list" role="group" aria-label="US states">
            {filteredStates.map((state) => (
              <label key={state}>
                <input
                  type="checkbox"
                  checked={criteria.us_states.includes(state)}
                  onChange={() => toggleState(state)}
                />
                {state}
              </label>
            ))}
          </div>
        </fieldset>
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
              placeholder="0"
              value={ageDraft.min_years}
              onChange={(event) =>
                setAgeDraft((current) => ({ ...current, min_years: event.target.value }))
              }
            />
          </label>
          <label>
            Minimum months
            <input
              type="number"
              min={0}
              max={11}
              placeholder="0"
              value={ageDraft.min_months}
              onChange={(event) =>
                setAgeDraft((current) => ({ ...current, min_months: event.target.value }))
              }
            />
          </label>
          <label>
            Maximum years
            <input
              type="number"
              min={0}
              max={100}
              placeholder="5"
              value={ageDraft.max_years}
              onChange={(event) =>
                setAgeDraft((current) => ({ ...current, max_years: event.target.value }))
              }
            />
          </label>
          <label>
            Maximum months
            <input
              type="number"
              min={0}
              max={11}
              placeholder="0"
              value={ageDraft.max_months}
              onChange={(event) =>
                setAgeDraft((current) => ({ ...current, max_months: event.target.value }))
              }
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
          value={criteria.candidate_limit}
          onChange={(event) =>
            setCriteria((current) => ({ ...current, candidate_limit: Number(event.target.value) }))
          }
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
